"""Derive auditable task runs and execution DAG nodes from captured chat events."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .conversation import classify_tool_status
from .costing import estimate_request_cost
from .models import ChatMessage, EvaluationJob, ExecutionNode, Session, TaskRun, ToolExecution
from .tool_grouping import execution_input, tool_operation
from .tracing import explicit_duration_ms


TASK_DERIVATION_VERSION = "task-v1"
NODE_DERIVATION_VERSION = "node-v1"


def _first_metadata(messages: Iterable[ChatMessage], *keys: str) -> Any:
    for message in reversed(list(messages)):
        metadata = message.metadata_ or {}
        for key in keys:
            value = metadata.get(key)
            if value not in (None, ""):
                return value
    return None


def _turn_id(message: ChatMessage) -> str:
    metadata = message.metadata_ or {}
    if metadata.get("turn_id"):
        return str(metadata["turn_id"])
    match = re.match(r"turn:([^:]+):", message.event_id or "")
    return match.group(1) if match else f"sequence-{message.sequence}"


def classify_task_kind(prompt: str) -> str:
    text = prompt.casefold()
    rules = (
        ("debug", ("debug", "fix", "bug", "报错", "错误", "修复", "失败")),
        ("code_change", ("implement", "add ", "build", "修改", "实现", "新增", "重构")),
        ("research", ("research", "search", "compare", "调研", "搜索", "比较", "方案")),
        ("operations", ("deploy", "server", "database", "发布", "部署", "数据库", "运维")),
        ("explanation", ("explain", "what is", "why", "解释", "说明", "为什么")),
    )
    return next((kind for kind, needles in rules if any(item in text for item in needles)), "general")


def classify_risk(messages: Iterable[ChatMessage]) -> str:
    tools = [message for message in messages if message.role == "tool"]
    if not tools:
        return "low"
    source = " ".join(
        f"{(message.metadata_ or {}).get('tool_name', '')} {(message.metadata_ or {}).get('tool_input', '')}"
        for message in tools
    ).casefold()
    external_writes = ("slack_send_message", "create_pr", "git push", "deploy", "delete", "remove-item", "gmail", "outlook", "teams")
    local_writes = ("apply_patch", "write", "edit", "git commit", "move-item", "mkdir")
    if any(item in source for item in external_writes):
        return "high"
    if any(item in source for item in local_writes):
        return "medium"
    return "low"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _usage(metadata: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
    input_tokens = _integer(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _integer(usage.get("output_tokens", usage.get("completion_tokens")))
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached = _integer(usage.get("cached_input_tokens", details.get("cached_tokens")))
    return input_tokens, cached, output_tokens


def _agent_identity(message: ChatMessage) -> tuple[str | None, str | None]:
    metadata = message.metadata_ or {}
    agent_id = metadata.get("agent_id") or metadata.get("subagent_id")
    parent = metadata.get("parent_agent_id") or metadata.get("parent_id")
    if not agent_id and message.role == "tool":
        match = re.search(r'(?i)(?:agent[_ -]?id|target)["\s:=]+([a-z0-9_./-]{3,255})', message.content[:4000])
        agent_id = match.group(1) if match else None
    return (str(agent_id) if agent_id else None, str(parent) if parent else None)


def _node_type(message: ChatMessage, operation: str | None) -> str:
    if message.role != "tool":
        return "request" if message.role == "user" else "response"
    name = str((message.metadata_ or {}).get("tool_name") or "").casefold()
    operation = (operation or "").casefold()
    if any(item in f"{name} {operation}" for item in ("spawn_agent", "followup_task", "send_message", "wait_agent")):
        return "agent"
    if any(item in f"{name} {operation}" for item in ("pytest", "test", "check", "verify")):
        return "validator"
    return "tool"


def _capture_completeness(group: list[ChatMessage], response: ChatMessage | None) -> float:
    tools = [message for message in group if message.role == "tool"]
    has_turn = any((message.metadata_ or {}).get("turn_id") for message in group)
    linked_tools = not tools or all((message.metadata_ or {}).get("tool_use_id") for message in tools)
    has_model = any((message.metadata_ or {}).get("model") or (message.metadata_ or {}).get("model_name") for message in group)
    has_duration = any(explicit_duration_ms(message.metadata_ or {}) is not None for message in group)
    score = 0.20 * has_turn + 0.25 * bool(response) + 0.25 * linked_tools + 0.15 * has_model + 0.15 * has_duration
    return round(score, 4)


def _groups(messages: list[ChatMessage]) -> list[list[ChatMessage]]:
    groups: list[list[ChatMessage]] = []
    current: list[ChatMessage] = []
    for message in sorted(messages, key=lambda item: (item.sequence, item.created_at)):
        if message.role == "user" and current:
            if any(item.role == "user" for item in current):
                groups.append(current)
            current = []
        current.append(message)
    if current and any(item.role == "user" for item in current):
        groups.append(current)
    return groups


def sync_session_task_runs(db: DbSession, session: Session) -> list[TaskRun]:
    """Idempotently derive task runs and nodes; caller owns the transaction."""
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.sequence, ChatMessage.created_at)
    ).all()
    archives = {
        item.chat_message_id: item
        for item in db.scalars(select(ToolExecution).where(ToolExecution.session_id == session.id)).all()
    }
    results: list[TaskRun] = []
    for group in _groups(messages):
        request = next(item for item in group if item.role == "user")
        response = next((item for item in reversed(group) if item.role == "assistant"), None)
        turn_id = _turn_id(request)
        task = db.scalar(select(TaskRun).where(TaskRun.session_id == session.id, TaskRun.turn_id == turn_id))
        if task is None:
            task = TaskRun(project_id=session.project_id, session_id=session.id, turn_id=turn_id)
            db.add(task)
            db.flush()

        model_name = _first_metadata(group, "model", "model_name")
        reasoning = _first_metadata(group, "reasoning_effort", "reasoning")
        route_arm = _first_metadata(group, "route_arm", "strategy_arm") or task.route_arm or "observed"
        policy_version = _first_metadata(group, "policy_version", "strategy_version")
        assignment_probability = _number(_first_metadata(group, "assignment_probability"))
        task.request_message_id = request.id
        task.response_message_id = response.id if response else None
        task.status = "completed" if response else "active"
        task.task_kind = classify_task_kind(request.content)
        task.risk_level = classify_risk(group)
        task.model_name = str(model_name)[:200] if model_name else None
        task.reasoning_effort = str(reasoning)[:40] if reasoning else None
        task.route_arm = str(route_arm)[:80]
        task.policy_version = str(policy_version)[:80] if policy_version else None
        task.assignment_probability = assignment_probability
        task.started_at = request.created_at
        task.ended_at = response.created_at if response else None
        task.capture_completeness = _capture_completeness(group, response)
        task.derivation_version = TASK_DERIVATION_VERSION
        task.metadata_ = {
            **(task.metadata_ or {}),
            "message_count": len(group),
            "tool_count": sum(item.role == "tool" for item in group),
            "capture_sources": sorted({str((item.metadata_ or {}).get("capture") or "unknown") for item in group}),
        }

        request_cost = estimate_request_cost(group)
        existing = {
            node.node_key: node
            for node in db.scalars(select(ExecutionNode).where(ExecutionNode.task_run_id == task.id)).all()
        }
        prior: ExecutionNode | None = None
        for message in group:
            node_key = message.event_id or f"message:{message.id}"
            node = existing.get(node_key)
            if node is None:
                node = ExecutionNode(task_run_id=task.id, node_key=node_key, sequence=message.sequence, node_type="tool")
                db.add(node)
                db.flush()
                existing[node_key] = node
            archive = archives.get(message.id)
            input_text = execution_input(archive) if archive else str((message.metadata_ or {}).get("tool_input") or "")
            operation = tool_operation(input_text) if message.role == "tool" else message.role
            agent_id, parent_agent_id = _agent_identity(message)
            metadata = dict(message.metadata_ or {})
            if parent_agent_id:
                metadata["parent_agent_id"] = parent_agent_id
            node.parent_node_id = prior.id if prior else None
            node.chat_message_id = message.id
            node.tool_execution_id = archive.id if archive else None
            node.branch_id = agent_id or metadata.get("branch_id")
            node.agent_id = agent_id
            node.node_type = _node_type(message, operation)
            node.operation = operation[:200] if operation else None
            node.status = classify_tool_status(metadata, message.content) if message.role == "tool" else ("completed" if message.role == "assistant" else "accepted")
            node.sequence = message.sequence
            node.started_at = message.created_at
            duration = explicit_duration_ms(metadata)
            node.duration_ms = duration
            node.ended_at = message.created_at if duration is None else None
            input_tokens, cached, output_tokens = _usage(metadata)
            if response and message.id == response.id:
                input_tokens = input_tokens if input_tokens is not None else request_cost["input_tokens"]
                cached = cached if cached is not None else request_cost["cached_input_tokens"]
                output_tokens = output_tokens if output_tokens is not None else request_cost["output_tokens"]
                node.cost_usd = request_cost["cost_usd"]
                node.cost_accuracy = request_cost["accuracy"]
            else:
                node.cost_usd = _number(metadata.get("cost_usd"))
                node.cost_accuracy = "usage" if node.cost_usd is not None else "unknown"
            node.input_tokens = input_tokens
            node.cached_input_tokens = cached
            node.output_tokens = output_tokens
            node.derivation_version = NODE_DERIVATION_VERSION
            node.metadata_ = metadata
            prior = node

        if task.status == "completed":
            job_key = f"{task.id}:evaluate:v1"
            if db.scalar(select(EvaluationJob).where(EvaluationJob.job_key == job_key)) is None:
                db.add(EvaluationJob(task_run_id=task.id, job_key=job_key))
        results.append(task)
    return results


def task_run_summary(task: TaskRun) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "turn_id": task.turn_id,
        "status": task.status,
        "task_kind": task.task_kind,
        "risk_level": task.risk_level,
        "model": task.model_name or "unknown",
        "reasoning_effort": task.reasoning_effort or "unknown",
        "route_arm": task.route_arm,
        "capture_completeness": task.capture_completeness,
        "quality_score": task.quality_score,
        "quality_confidence": task.quality_confidence,
        "quality_status": task.quality_status,
        "metadata": json.loads(json.dumps(task.metadata_ or {}, default=str)),
    }
