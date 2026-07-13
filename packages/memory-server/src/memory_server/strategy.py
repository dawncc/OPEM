"""Versioned, conservative execution-route recommendations and evaluation."""

from __future__ import annotations

import hashlib
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from memory_common.config import get_settings
from memory_common.schemas import RouteRecommendationRequest

from .models import (
    ExperimentAssignment,
    ExecutionNode,
    OutcomeEvidence,
    PathScore,
    Project,
    Session,
    StrategyEvaluation,
    StrategyVersion,
    TaskRun,
)
from .trajectory import classify_task_kind


BASELINE_VERSION = "baseline-v1"
BASELINE_CONFIG = {
    "route_arm": "observed",
    "model_level": None,
    "reasoning_effort": None,
    "max_agents": 0,
    "max_tool_calls": 0,
    "verification_mode": "targeted",
}


def _project(db: DbSession, name: str) -> Project:
    project = db.scalar(select(Project).where(Project.name == name))
    if project is None:
        project = Project(name=name)
        db.add(project)
        db.flush()
    return project


def ensure_baseline_strategy(db: DbSession, project: Project) -> StrategyVersion:
    strategy = db.scalar(select(StrategyVersion).where(
        StrategyVersion.project_id == project.id,
        StrategyVersion.version == BASELINE_VERSION,
    ))
    if strategy is None:
        strategy = StrategyVersion(
            project_id=project.id,
            version=BASELINE_VERSION,
            name="Observed baseline",
            status="baseline",
            config=BASELINE_CONFIG,
            automatic=False,
        )
        db.add(strategy)
        db.flush()
    return strategy


def _task_run(db: DbSession, project: Project, external_session_id: str | None, turn_id: str | None) -> TaskRun | None:
    if not external_session_id or not turn_id:
        return None
    return db.scalar(
        select(TaskRun)
        .join(Session, TaskRun.session_id == Session.id)
        .where(
            TaskRun.project_id == project.id,
            Session.external_session_id == external_session_id,
            TaskRun.turn_id == turn_id,
        )
    )


def _fraction(key: str) -> float:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def recommend_route(db: DbSession, payload: RouteRecommendationRequest) -> tuple[ExperimentAssignment, StrategyVersion, dict[str, Any], list[str]]:
    settings = get_settings()
    project = _project(db, payload.project)
    baseline = ensure_baseline_strategy(db, project)
    selected = baseline
    mode = "observe"
    probability = 1.0
    reasons = ["No eligible promoted strategy; observing the current route."]

    active = db.scalar(select(StrategyVersion).where(
        StrategyVersion.project_id == project.id,
        StrategyVersion.status == "active",
    ).order_by(StrategyVersion.created_at.desc()))
    shadow = db.scalar(select(StrategyVersion).where(
        StrategyVersion.project_id == project.id,
        StrategyVersion.status.in_(("shadow", "canary")),
    ).order_by(StrategyVersion.created_at.desc()))
    if active:
        selected, mode = active, "active"
        reasons = ["Using the currently active, versioned strategy."]
    elif shadow:
        selected, mode = shadow, "shadow"
        probability = 0.0
        reasons = ["Candidate is returned for shadow comparison and must not change execution."]
        strong_cases = int((shadow.metrics_snapshot or {}).get("strong_cases", 0))
        if (
            shadow.status == "canary"
            and settings.memory_strategy_canary_enabled
            and payload.risk_level == "low"
            and strong_cases >= settings.memory_strategy_min_canary_cases
        ):
            probability = max(0.0, min(0.2, settings.memory_strategy_canary_fraction))
            key = f"{payload.project}:{payload.session_id}:{payload.turn_id}:{shadow.version}"
            if _fraction(key) < probability:
                mode = "canary"
                reasons = ["Selected for bounded low-risk canary by deterministic assignment."]
            else:
                selected, mode = baseline, "observe"
                reasons = ["Outside the configured canary sample; using observed baseline."]

    config = {**BASELINE_CONFIG, **(selected.config or {})}
    if payload.risk_level == "high" and mode in {"canary", "active"}:
        selected, config, mode, probability = baseline, dict(BASELINE_CONFIG), "observe", 1.0
        reasons = ["High-risk tasks are pinned to the conservative observed baseline."]
    configured_model = config.get("model_level")
    if configured_model and payload.available_model_levels and configured_model not in payload.available_model_levels:
        config["model_level"] = payload.current_model
        reasons.append("Configured model level is unavailable; retaining the current model.")
    elif not configured_model:
        config["model_level"] = payload.current_model

    task = _task_run(db, project, payload.session_id, payload.turn_id)
    assignment = ExperimentAssignment(
        task_run_id=task.id if task else None,
        strategy_version_id=selected.id,
        baseline_strategy_id=baseline.id if selected.id != baseline.id else None,
        external_session_id=payload.session_id,
        turn_id=payload.turn_id,
        arm=str(config.get("route_arm") or "observed")[:80],
        mode=mode,
        assignment_probability=probability,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment, selected, config, reasons


def _interval(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    mean = statistics.fmean(values)
    if len(values) == 1:
        return mean, 0.0, 1.0
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return mean, max(0.0, mean - 1.96 * standard_error), min(1.0, mean + 1.96 * standard_error)


def evaluate_execution_strategies(db: DbSession, project_name: str | None = None, persist: bool = False) -> list[dict[str, Any]]:
    stmt = select(TaskRun, Project.name).join(Project, TaskRun.project_id == Project.id).order_by(TaskRun.started_at)
    if project_name:
        stmt = stmt.where(Project.name == project_name)
    rows = db.execute(stmt).all()
    strong_task_ids = set(db.scalars(
        select(OutcomeEvidence.task_run_id).where(
            OutcomeEvidence.strength == "strong",
            OutcomeEvidence.metric == "task_success",
        ).distinct()
    ).all())
    grouped: dict[tuple[str, str, str], list[TaskRun]] = defaultdict(list)
    for task, name in rows:
        bucket = f"{task.task_kind}:{task.risk_level}"
        grouped[(name, task.route_arm or "observed", bucket)].append(task)

    output: list[dict[str, Any]] = []
    for (name, arm, bucket), tasks in sorted(grouped.items()):
        quality = [task.quality_score for task in tasks if task.quality_score is not None]
        mean, lcb, ucb = _interval(quality)
        task_ids = [task.id for task in tasks]
        costs = dict(db.execute(
            select(ExecutionNode.task_run_id, func.coalesce(func.sum(ExecutionNode.cost_usd), 0.0))
            .where(ExecutionNode.task_run_id.in_(task_ids))
            .group_by(ExecutionNode.task_run_id)
        ).all()) if task_ids else {}
        latencies = [
            max(0.0, (task.ended_at - task.started_at).total_seconds() * 1000)
            for task in tasks if task.ended_at
        ]
        strong_cases = sum(task.id in strong_task_ids for task in tasks)
        reasons = []
        settings = get_settings()
        if strong_cases < settings.memory_strategy_min_shadow_cases:
            reasons.append(f"strong cases {strong_cases} < shadow minimum {settings.memory_strategy_min_shadow_cases}")
        if lcb is None:
            reasons.append("no task-success evidence")
        item = {
            "project": name,
            "route_arm": arm,
            "task_bucket": bucket,
            "cases": len(tasks),
            "labeled_cases": len(quality),
            "strong_cases": strong_cases,
            "quality_mean": None if mean is None else round(mean, 4),
            "quality_lcb": None if lcb is None else round(lcb, 4),
            "quality_ucb": None if ucb is None else round(ucb, 4),
            "severe_failure_rate": round(sum(task.severe_failure for task in tasks) / len(tasks), 4),
            "average_cost_usd": round(statistics.fmean([float(costs.get(task.id, 0.0)) for task in tasks]), 8),
            "average_latency_ms": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "promotion_allowed": not reasons,
            "gate_reasons": reasons,
        }
        output.append(item)
        if persist:
            project = db.scalar(select(Project).where(Project.name == name))
            strategy = db.scalar(select(StrategyVersion).where(
                StrategyVersion.project_id == project.id,
                StrategyVersion.version == BASELINE_VERSION if arm == "observed" else StrategyVersion.version == arm,
            )) if project else None
            db.add(StrategyEvaluation(
                strategy_version_id=strategy.id if strategy else None,
                project_id=project.id,
                task_bucket=bucket,
                cases=item["cases"],
                strong_cases=strong_cases,
                quality_mean=mean,
                quality_lcb=lcb,
                quality_ucb=ucb,
                severe_failure_rate=item["severe_failure_rate"],
                average_cost_usd=item["average_cost_usd"],
                average_latency_ms=item["average_latency_ms"],
                promotion_allowed=item["promotion_allowed"],
                gate_reasons=reasons,
            ))
    if persist:
        db.commit()
    return output


def propose_shadow_strategy(db: DbSession, project_name: str) -> tuple[StrategyVersion | None, list[str]]:
    """Create an auditable budget candidate only after enough strong evidence exists."""
    settings = get_settings()
    project = db.scalar(select(Project).where(Project.name == project_name))
    if project is None:
        return None, ["project not found"]
    baseline = ensure_baseline_strategy(db, project)
    version = "shadow-auto-v1"
    existing = db.scalar(select(StrategyVersion).where(
        StrategyVersion.project_id == project.id, StrategyVersion.version == version,
    ))
    if existing:
        return existing, ["existing shadow candidate returned"]
    task_ids = db.scalars(select(TaskRun.id).where(TaskRun.project_id == project.id)).all()
    strong_ids = set(db.scalars(select(OutcomeEvidence.task_run_id).where(
        OutcomeEvidence.task_run_id.in_(task_ids),
        OutcomeEvidence.metric == "task_success",
        OutcomeEvidence.strength == "strong",
    )).all()) if task_ids else set()
    if len(strong_ids) < settings.memory_strategy_min_shadow_cases:
        return None, [f"strong cases {len(strong_ids)} < shadow minimum {settings.memory_strategy_min_shadow_cases}"]
    nodes = db.scalars(select(ExecutionNode).where(ExecutionNode.task_run_id.in_(strong_ids))).all()
    per_task_tools: dict[Any, int] = defaultdict(int)
    per_task_agents: dict[Any, int] = defaultdict(int)
    for node in nodes:
        if node.node_type in {"tool", "validator", "agent"}:
            per_task_tools[node.task_run_id] += 1
        if node.node_type == "agent":
            per_task_agents[node.task_run_id] += 1
    redundancies = db.scalars(select(PathScore.redundancy).where(PathScore.task_run_id.in_(strong_ids))).all()
    average_redundancy = statistics.fmean(redundancies) if redundancies else 0.0
    average_tools = statistics.fmean([per_task_tools.get(task_id, 0) for task_id in strong_ids])
    observed_agents = max((per_task_agents.get(task_id, 0) for task_id in strong_ids), default=0)
    tool_factor = 0.85 if average_redundancy >= 0.30 else 1.0
    config = {
        "route_arm": version,
        "model_level": None,
        "reasoning_effort": None,
        "max_agents": min(2, observed_agents),
        "max_tool_calls": max(4, math.ceil(average_tools * tool_factor)),
        "verification_mode": "targeted",
    }
    candidate = StrategyVersion(
        project_id=project.id,
        parent_id=baseline.id,
        version=version,
        name="Automatically proposed bounded route",
        status="shadow",
        config=config,
        metrics_snapshot={
            "strong_cases": len(strong_ids),
            "average_tools": round(average_tools, 3),
            "average_redundancy": round(average_redundancy, 4),
        },
        automatic=True,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate, ["candidate created in shadow mode; no runtime behavior changed"]


def evolution_summary(db: DbSession, project_name: str | None = None) -> dict[str, Any]:
    task_stmt = select(TaskRun)
    if project_name:
        task_stmt = task_stmt.join(Project).where(Project.name == project_name)
    tasks = db.scalars(task_stmt.order_by(TaskRun.started_at.desc())).all()
    known = [task for task in tasks if task.quality_score is not None]
    strong_stmt = select(OutcomeEvidence.task_run_id).join(TaskRun).where(OutcomeEvidence.strength == "strong")
    if project_name:
        strong_stmt = strong_stmt.join(Project).where(Project.name == project_name)
    return {
        "tasks": len(tasks),
        "labeled_tasks": len(known),
        "strong_tasks": len(set(db.scalars(strong_stmt).all())),
        "unknown_tasks": len(tasks) - len(known),
        "average_capture_completeness": round(statistics.fmean([task.capture_completeness for task in tasks]), 4) if tasks else 0.0,
        "strategies": db.scalar(select(func.count()).select_from(StrategyVersion)) or 0,
        "assignments": db.scalar(select(func.count()).select_from(ExperimentAssignment)) or 0,
        "generated_at": datetime.now(timezone.utc),
    }
