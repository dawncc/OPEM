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
def memory_recall(query: str, project: str | None = None, limit: int = 8) -> dict:
    """Recall durable memories. Use the returned chat-memory context as historical context, verify it, then continue the current task."""
    flush_pending()
    response = httpx.post(f"{SERVER_URL}/api/v1/recall", json={"query": query, "project": project, "limit": limit}, timeout=15)
    response.raise_for_status()
    return response.json()


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
def memory_chat_submit(project: str, session_id: str, messages: list[dict], source_host: str | None = None) -> dict:
    """Store the complete user, assistant, system, and tool chat transcript separately from summarized Memory."""
    normalized = []
    for index, message in enumerate(messages):
        normalized.append({
            "role": message.get("role", "user"),
            "content": str(message.get("content", "")),
            "sequence": int(message.get("sequence", index)),
            "created_at": message.get("created_at"),
            "metadata": message.get("metadata", {}),
        })
    payload = {"project": project, "session_id": session_id, "source_host": source_host or socket.gethostname(), "messages": normalized}
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
