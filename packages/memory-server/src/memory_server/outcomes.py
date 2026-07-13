"""Evidence-first task outcome derivation for zero-label cold starts."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

from memory_common.schemas import OutcomeEvidenceCreate

from .models import EvaluationJob, ExecutionNode, OutcomeEvidence, Project, Session, TaskRun


RULE_EVALUATOR_VERSION = "rules-v1"
STRENGTH_WEIGHT = {"strong": 1.0, "medium": 0.6, "weak": 0.25}


def _key(*parts: Any) -> str:
    return hashlib.sha256(":".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def _add_evidence(
    db: DbSession,
    task: TaskRun,
    *,
    evidence_key: str,
    evidence_type: str,
    metric: str,
    value: float,
    confidence: float,
    strength: str,
    source_type: str,
    source_ref: str | None = None,
    rationale: str | None = None,
    independence_group: str = "default",
    execution_node_id=None,
    metadata: dict | None = None,
    evaluator_version: str = RULE_EVALUATOR_VERSION,
) -> tuple[OutcomeEvidence, bool]:
    existing = db.scalar(select(OutcomeEvidence).where(OutcomeEvidence.evidence_key == evidence_key))
    if existing:
        return existing, True
    item = OutcomeEvidence(
        task_run_id=task.id,
        execution_node_id=execution_node_id,
        evidence_key=evidence_key,
        evidence_type=evidence_type,
        metric=metric,
        value=max(-1.0, min(1.0, float(value))),
        confidence=max(0.0, min(1.0, float(confidence))),
        strength=strength,
        source_type=source_type,
        source_ref=source_ref,
        rationale=rationale,
        independence_group=independence_group,
        evaluator_version=evaluator_version,
        metadata_=metadata or {},
    )
    db.add(item)
    db.flush()
    return item, False


def derive_rule_evidence(db: DbSession, task: TaskRun) -> list[OutcomeEvidence]:
    """Extract conservative evidence. Missing evidence never becomes success."""
    task = db.scalar(
        select(TaskRun)
        .where(TaskRun.id == task.id)
        .options(selectinload(TaskRun.nodes).selectinload(ExecutionNode.chat_message), selectinload(TaskRun.response_message))
    ) or task
    created: list[OutcomeEvidence] = []
    for node in task.nodes:
        if node.node_type not in {"tool", "validator", "agent"} or not node.chat_message:
            continue
        operation = (node.operation or "").casefold()
        tool_input = str((node.metadata_ or {}).get("tool_input") or (node.metadata_ or {}).get("input") or "").casefold()
        content = node.chat_message.content.casefold()
        test_like = any(item in f"{operation} {tool_input}" for item in ("pytest", "unittest", "jest", "vitest", " test"))
        passed = bool(re.search(r"\b\d+\s+passed\b|\btests?\s+passed\b|\bbuild succeeded\b", content))
        failed = bool(re.search(r"\b\d+\s+failed\b|\btests?\s+failed\b|\bbuild failed\b", content))
        if test_like and (passed or failed):
            item, _ = _add_evidence(
                db, task,
                evidence_key=_key(task.id, node.id, "deterministic-test", RULE_EVALUATOR_VERSION),
                evidence_type="deterministic_validator",
                metric="task_success",
                value=-1.0 if failed else 1.0,
                confidence=0.98,
                strength="strong",
                source_type="tool_output",
                source_ref=str(node.chat_message_id),
                rationale="Captured deterministic test/build summary.",
                independence_group="deterministic_validation",
                execution_node_id=node.id,
            )
            created.append(item)
        if node.status == "failed":
            item, _ = _add_evidence(
                db, task,
                evidence_key=_key(task.id, node.id, "tool-failure", RULE_EVALUATOR_VERSION),
                evidence_type="tool_failure",
                metric="tool_reliability",
                value=-1.0,
                confidence=0.95,
                strength="strong",
                source_type="tool_status",
                source_ref=str(node.chat_message_id),
                rationale="The tool execution failed; this does not by itself mean the task failed.",
                independence_group=f"tool:{node.id}",
                execution_node_id=node.id,
            )
            created.append(item)

    final = task.response_message.content.casefold() if task.response_message else ""
    if final and re.search(r"\b(blocked|unable to|could not complete)\b|无法完成|被阻塞|未能完成", final):
        item, _ = _add_evidence(
            db, task,
            evidence_key=_key(task.id, "reported-blocker", RULE_EVALUATOR_VERSION),
            evidence_type="agent_reported_blocker",
            metric="task_success",
            value=-0.7,
            confidence=0.65,
            strength="medium",
            source_type="final_response",
            source_ref=str(task.response_message_id),
            rationale="The final response explicitly reported that completion was blocked.",
            independence_group="reported_outcome",
        )
        created.append(item)

    next_task = db.scalar(
        select(TaskRun)
        .where(TaskRun.session_id == task.session_id, TaskRun.started_at > task.started_at)
        .order_by(TaskRun.started_at)
        .options(selectinload(TaskRun.request_message))
        .limit(1)
    )
    followup = next_task.request_message.content.casefold() if next_task and next_task.request_message else ""
    if followup and re.search(r"不对|还是失败|没有解决|重新修|doesn['’]t work|still fail|try again", followup):
        item, _ = _add_evidence(
            db, task,
            evidence_key=_key(task.id, next_task.id, "correction-candidate", RULE_EVALUATOR_VERSION),
            evidence_type="user_correction_candidate",
            metric="task_success",
            value=-0.6,
            confidence=0.45,
            strength="weak",
            source_type="next_user_turn",
            source_ref=str(next_task.request_message_id),
            rationale="The next user turn appears to request correction; kept as weak evidence.",
            independence_group="user_followup",
        )
        created.append(item)
    elif followup and re.search(r"谢谢|可以了|解决了|很好|works now|thank you|looks good", followup):
        item, _ = _add_evidence(
            db, task,
            evidence_key=_key(task.id, next_task.id, "acceptance-candidate", RULE_EVALUATOR_VERSION),
            evidence_type="user_acceptance_candidate",
            metric="task_success",
            value=0.8,
            confidence=0.55,
            strength="medium",
            source_type="next_user_turn",
            source_ref=str(next_task.request_message_id),
            rationale="The next user turn explicitly appears to accept the result.",
            independence_group="user_followup",
        )
        created.append(item)
    return created


def aggregate_task_quality(db: DbSession, task: TaskRun) -> TaskRun:
    evidence = db.scalars(
        select(OutcomeEvidence).where(OutcomeEvidence.task_run_id == task.id, OutcomeEvidence.metric == "task_success")
    ).all()
    strongest: dict[str, OutcomeEvidence] = {}
    for item in evidence:
        weight = item.confidence * STRENGTH_WEIGHT.get(item.strength, 0.0)
        prior = strongest.get(item.independence_group)
        prior_weight = prior.confidence * STRENGTH_WEIGHT.get(prior.strength, 0.0) if prior else -1.0
        if weight > prior_weight:
            strongest[item.independence_group] = item
    weighted = [
        (item.value, item.confidence * STRENGTH_WEIGHT.get(item.strength, 0.0))
        for item in strongest.values()
    ]
    total = sum(weight for _, weight in weighted)
    if not weighted or total <= 0:
        task.quality_score = None
        task.quality_confidence = 0.0
        task.quality_status = "unknown"
        task.severe_failure = False
        return task
    signed = sum(value * weight for value, weight in weighted) / total
    task.quality_score = round((signed + 1.0) / 2.0, 4)
    task.quality_confidence = round(1.0 - math.exp(-total), 4)
    task.severe_failure = any(item.value <= -0.95 and item.strength == "strong" for item in strongest.values())
    if task.quality_confidence < 0.35:
        task.quality_status = "uncertain"
    elif task.quality_score >= 0.75:
        task.quality_status = "success"
    elif task.quality_score <= 0.25:
        task.quality_status = "failed"
    else:
        task.quality_status = "mixed"
    return task


def record_outcome_evidence(db: DbSession, payload: OutcomeEvidenceCreate) -> tuple[TaskRun, OutcomeEvidence, bool]:
    task = db.scalar(
        select(TaskRun)
        .join(Session, TaskRun.session_id == Session.id)
        .join(Project, TaskRun.project_id == Project.id)
        .where(Project.name == payload.project, Session.external_session_id == payload.session_id, TaskRun.turn_id == payload.turn_id)
    )
    if task is None:
        raise LookupError("task run not found; capture the user turn before reporting its outcome")
    evidence_key = payload.idempotency_key or _key(
        task.id, payload.evidence_type, payload.metric, payload.source_ref, payload.value, payload.rationale
    )
    item, duplicate = _add_evidence(
        db, task,
        evidence_key=evidence_key,
        evidence_type=payload.evidence_type,
        metric=payload.metric,
        value=payload.value,
        confidence=payload.confidence,
        strength=payload.strength,
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        rationale=payload.rationale,
        independence_group=payload.independence_group,
        metadata=payload.metadata,
        evaluator_version="explicit-v1",
    )
    aggregate_task_quality(db, task)
    db.commit()
    db.refresh(item)
    return task, item, duplicate


def process_evaluation_job(db: DbSession, job: EvaluationJob) -> None:
    task = db.get(TaskRun, job.task_run_id)
    if task is None:
        job.status = "failed"
        job.error = "task run not found"
        job.finished_at = datetime.now(timezone.utc)
        return
    derive_rule_evidence(db, task)
    aggregate_task_quality(db, task)
    from .path_scoring import score_task_paths
    score_task_paths(db, task)
    job.status = "completed"
    job.error = None
    job.finished_at = datetime.now(timezone.utc)
