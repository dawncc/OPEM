"""Observable path scoring. Causal fields remain empty without interventions."""

from __future__ import annotations

import re
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

from memory_common.schemas import PathInterventionCreate

from .models import ExecutionNode, OutcomeEvidence, PathScore, Project, Session, TaskRun


PATH_DERIVATION_VERSION = "path-v1"


def _tokens(value: str) -> set[str]:
    text = value.casefold()
    latin = set(re.findall(r"[a-z0-9_./:-]{2,}", text))
    chinese = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return latin | chinese


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / max(len(left | right), 1)


def score_task_paths(db: DbSession, task: TaskRun) -> list[PathScore]:
    task = db.scalar(
        select(TaskRun)
        .where(TaskRun.id == task.id)
        .options(selectinload(TaskRun.nodes).selectinload(ExecutionNode.chat_message), selectinload(TaskRun.response_message))
    ) or task
    final_tokens = _tokens(task.response_message.content if task.response_message else "")
    prior_outputs: list[set[str]] = []
    scored: list[PathScore] = []
    for node in sorted(task.nodes, key=lambda item: item.sequence):
        if node.node_type not in {"tool", "agent", "validator"}:
            continue
        output_tokens = _tokens(node.chat_message.content if node.chat_message else "")
        final_overlap = _jaccard(output_tokens, final_tokens) if final_tokens else 0.0
        redundancy = max((_jaccard(output_tokens, prior) for prior in prior_outputs), default=0.0)
        novelty = 1.0 - redundancy if output_tokens else 0.0
        validation = 1.0 if node.node_type == "validator" else 0.0
        if node.node_type != "validator" and any(item in (node.operation or "").casefold() for item in ("test", "check", "verify")):
            validation = 0.8
        downstream = 0.5 if task.response_message_id else 0.0
        relatedness = min(1.0, 0.40 * final_overlap + 0.25 * downstream + 0.20 * novelty + 0.15 * validation)
        confidence = min(0.65, 0.15 + 0.35 * task.capture_completeness + 0.20 * bool(final_tokens))
        path_key = node.node_key
        item = db.scalar(select(PathScore).where(
            PathScore.task_run_id == task.id,
            PathScore.path_key == path_key,
            PathScore.derivation_version == PATH_DERIVATION_VERSION,
        ))
        if item is None:
            item = PathScore(task_run_id=task.id, execution_node_id=node.id, path_key=path_key)
            db.add(item)
        item.relatedness = round(relatedness, 4)
        item.downstream_dependency = round(downstream, 4)
        item.novelty = round(novelty, 4)
        item.validation_value = round(validation, 4)
        item.redundancy = round(redundancy, 4)
        if item.evidence_method == "observational":
            item.necessity = None
            item.importance = None
            item.efficiency = None
        item.direct_cost_usd = round(node.cost_usd or 0.0, 8)
        item.critical_path_ms = round(node.duration_ms or 0.0, 3)
        item.evidence_method = item.evidence_method or "observational"
        item.confidence = round(confidence, 4)
        item.derivation_version = PATH_DERIVATION_VERSION
        scored.append(item)
        if output_tokens:
            prior_outputs.append(output_tokens)
    return scored


def record_path_intervention(db: DbSession, payload: PathInterventionCreate) -> tuple[PathScore, OutcomeEvidence, bool]:
    task = db.scalar(
        select(TaskRun)
        .join(Session, TaskRun.session_id == Session.id)
        .join(Project, TaskRun.project_id == Project.id)
        .where(Project.name == payload.project, Session.external_session_id == payload.session_id, TaskRun.turn_id == payload.turn_id)
    )
    if task is None:
        raise LookupError("task run not found")
    score = db.scalar(select(PathScore).where(
        PathScore.task_run_id == task.id,
        PathScore.path_key == payload.path_key,
    ).order_by(PathScore.updated_at.desc()))
    if score is None:
        raise LookupError("path score not found")
    evidence_key = payload.idempotency_key or hashlib.sha256(
        f"{task.id}:{payload.path_key}:{payload.method}:{payload.full_quality}:{payload.counterfactual_quality}".encode("utf-8")
    ).hexdigest()
    evidence = db.scalar(select(OutcomeEvidence).where(OutcomeEvidence.evidence_key == evidence_key))
    duplicate = evidence is not None
    delta = round(payload.full_quality - payload.counterfactual_quality, 4)
    if evidence is None:
        evidence = OutcomeEvidence(
            task_run_id=task.id,
            execution_node_id=score.execution_node_id,
            evidence_key=evidence_key,
            evidence_type="path_intervention",
            metric="path_quality_delta",
            value=max(-1.0, min(1.0, delta)),
            confidence=payload.confidence,
            strength="strong",
            source_type=payload.method,
            source_ref=payload.path_key,
            rationale=payload.rationale,
            independence_group=f"intervention:{payload.path_key}",
            evaluator_version="intervention-v1",
        )
        db.add(evidence)
        db.flush()
    score.necessity = max(0.0, delta)
    score.importance = delta
    score.evidence_method = payload.method
    score.confidence = payload.confidence
    score.efficiency = round(delta / score.direct_cost_usd, 6) if score.direct_cost_usd > 0 else None
    db.commit()
    db.refresh(score)
    return score, evidence, duplicate
