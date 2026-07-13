import argparse
import hashlib
import json
import os
import socket
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP

from memory_common.pending import append_pending, drain_pending

mcp = FastMCP("codex-memory")
SERVER_URL = os.environ.get("MEMORY_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")


def post_observation(payload: dict) -> dict:
    response = httpx.post(f"{SERVER_URL}/api/v1/observations", json=payload, timeout=10)
    response.raise_for_status()
    return response.json()


def post_queued(payload: dict) -> dict:
    endpoint = payload.pop("_queue_endpoint", "/api/v1/observations")
    response = httpx.post(f"{SERVER_URL}{endpoint}", json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def flush_pending() -> int:
    return drain_pending(post_queued)


def submit(payload: dict) -> dict:
    payload.setdefault("source_host", socket.gethostname())
    if not payload.get("idempotency_key"):
        stable = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        payload["idempotency_key"] = hashlib.sha256(stable.encode()).hexdigest()
    try:
        flushed = flush_pending()
        result = post_observation(payload)
        result["flushed_pending"] = flushed
        return result
    except Exception as exc:
        append_pending(payload)
        return {"status": "queued_locally", "error": str(exc), "idempotency_key": payload["idempotency_key"]}


def post_or_queue(endpoint: str, payload: dict) -> dict:
    """Preserve write-only evolution evidence while the LAN server is offline."""
    try:
        flushed = flush_pending()
        response = httpx.post(f"{SERVER_URL}{endpoint}", json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        result["flushed_pending"] = flushed
        return result
    except Exception as exc:
        append_pending({"_queue_endpoint": endpoint, **payload})
        return {"status": "queued_locally", "error": str(exc)}


@mcp.tool()
def memory_submit(
    content: str,
    kind: Literal["observation", "decision", "learning", "problem", "solution", "session_summary"],
    project: str,
    session_id: str | None = None,
    concepts: list[str] | None = None,
    files: list[str] | None = None,
    importance: int = 3,
) -> dict:
    """Save a durable project observation, decision, learning, problem, solution, or summary."""
    return submit({"content": content, "kind": kind, "project": project, "session_id": session_id, "concepts": concepts or [], "files": files or [], "importance": importance})


@mcp.tool()
def memory_recall(
    query: str,
    project: str | None = None,
    limit: int = 8,
    session_id: str | None = None,
    turn_id: str | None = None,
    policy_version: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Recall durable memories. Use the returned chat-memory context as historical context, verify it, then continue the current task."""
    flush_pending()
    response = httpx.post(f"{SERVER_URL}/api/v1/recall", json={
        "query": query, "project": project, "limit": limit,
        "session_id": session_id, "turn_id": turn_id,
        "policy_version": policy_version, "idempotency_key": idempotency_key,
    }, timeout=15)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def memory_feedback(
    memory_id: str,
    outcome: Literal["helpful", "irrelevant", "harmful"],
    query: str | None = None,
    reason: str | None = None,
    session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Report recall utility so future ranking can evolve from explicit evidence."""
    payload = {
        "memory_id": memory_id, "outcome": outcome, "query": query,
        "reason": reason, "session_id": session_id, "idempotency_key": idempotency_key,
    }
    response = httpx.post(f"{SERVER_URL}/api/v1/memories/feedback", json=payload, timeout=10)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def memory_task_outcome(
    project: str,
    session_id: str,
    turn_id: str,
    evidence_type: str,
    value: float,
    metric: str = "task_success",
    confidence: float = 1.0,
    strength: Literal["strong", "medium", "weak"] = "strong",
    source_type: str = "agent_report",
    source_ref: str | None = None,
    rationale: str | None = None,
    independence_group: str = "explicit_outcome",
    idempotency_key: str | None = None,
) -> dict:
    """Report evidence-backed task outcomes; absence of a report is never treated as success."""
    return post_or_queue("/api/v1/tasks/outcomes", {
        "project": project, "session_id": session_id, "turn_id": turn_id,
        "evidence_type": evidence_type, "metric": metric, "value": value,
        "confidence": confidence, "strength": strength, "source_type": source_type,
        "source_ref": source_ref, "rationale": rationale,
        "independence_group": independence_group, "idempotency_key": idempotency_key,
    })


@mcp.tool()
def memory_route_recommend(
    project: str,
    task: str,
    session_id: str | None = None,
    turn_id: str | None = None,
    task_kind: str | None = None,
    risk_level: Literal["unknown", "low", "medium", "high"] = "unknown",
    available_model_levels: list[str] | None = None,
    current_model: str | None = None,
) -> dict:
    """Return a versioned execution-route recommendation; observe/shadow modes must not change execution."""
    response = httpx.post(f"{SERVER_URL}/api/v1/routes/recommend", json={
        "project": project, "task": task, "session_id": session_id, "turn_id": turn_id,
        "task_kind": task_kind, "risk_level": risk_level,
        "available_model_levels": available_model_levels or [], "current_model": current_model,
    }, timeout=10)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def memory_path_intervention(
    project: str,
    session_id: str,
    turn_id: str,
    path_key: str,
    method: Literal["paired_replay", "randomized"],
    full_quality: float,
    counterfactual_quality: float,
    confidence: float = 1.0,
    idempotency_key: str | None = None,
    rationale: str | None = None,
) -> dict:
    """Record a controlled path ablation; use only for paired replay or randomized evidence."""
    return post_or_queue("/api/v1/paths/interventions", {
        "project": project, "session_id": session_id, "turn_id": turn_id,
        "path_key": path_key, "method": method,
        "full_quality": full_quality, "counterfactual_quality": counterfactual_quality,
        "confidence": confidence, "idempotency_key": idempotency_key, "rationale": rationale,
    })


@mcp.tool()
def memory_session_end(
    project: str,
    session_id: str,
    summary: str,
    completed: list[str] | None = None,
    decisions: list[str] | None = None,
    unresolved: list[str] | None = None,
    files: list[str] | None = None,
) -> dict:
    """Save a structured final or pre-compaction summary for a Codex session."""
    sections = [summary.strip()]
    for title, values in (("已完成", completed), ("决策", decisions), ("未解决", unresolved)):
        if values:
            sections.append(f"{title}：\n" + "\n".join(f"- {value}" for value in values))
    return submit({"content": "\n\n".join(sections), "kind": "session_summary", "project": project, "session_id": session_id, "concepts": [], "files": files or [], "importance": 4})


@mcp.tool()
def memory_chat_submit(
    project: str, session_id: str, messages: list[dict], source_host: str | None = None,
    session_status: Literal["active", "completed"] | None = None,
    session_summary: str | None = None,
    deduplicate_by_content: bool = False,
) -> dict:
    """Store the complete user, assistant, system, and tool chat transcript separately from summarized Memory."""
    normalized = []
    for index, message in enumerate(messages):
        normalized.append({
            "role": message.get("role", "user"),
            "content": str(message.get("content", "")),
            "sequence": int(message["sequence"]) if message.get("sequence") is not None else index,
            "event_id": message.get("event_id"),
            "created_at": message.get("created_at"),
            "duration_ms": message.get("duration_ms"),
            "metadata": message.get("metadata", {}),
        })
    payload = {
        "project": project, "session_id": session_id,
        "source_host": source_host or socket.gethostname(), "messages": normalized,
        "session_status": session_status, "session_summary": session_summary,
        "deduplicate_by_content": deduplicate_by_content,
    }
    try:
        flushed = flush_pending()
        response = httpx.post(f"{SERVER_URL}/api/v1/chat/messages", json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        result["flushed_pending"] = flushed
        return result
    except Exception as exc:
        payload["_queue_endpoint"] = "/api/v1/chat/messages"
        append_pending(payload)
        return {"status": "queued_locally", "error": str(exc), "message_count": len(normalized)}


@mcp.tool()
def memory_health() -> dict:
    """Check Memory Server connectivity and flush locally queued observations."""
    flushed = flush_pending()
    response = httpx.get(f"{SERVER_URL}/api/v1/health", timeout=5)
    response.raise_for_status()
    result = response.json()
    result["flushed_pending"] = flushed
    return result


def run() -> None:
    global SERVER_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=SERVER_URL)
    args = parser.parse_args()
    SERVER_URL = args.server.rstrip("/")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
