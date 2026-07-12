"""Codex Stop/PreCompact hook: persist an available summary without blocking Codex."""
import hashlib
import json
import os
import socket
import sys
from pathlib import Path

import httpx


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    project = payload.get("project") or Path(payload.get("cwd") or os.getcwd()).name
    session_id = str(payload.get("session_id") or payload.get("thread_id") or "unknown")
    summary = payload.get("summary") or payload.get("message") or "Codex session ended; no generated summary was available."
    observation = {
        "content": summary, "kind": "session_summary", "project": project, "session_id": session_id,
        "concepts": [], "files": payload.get("files", []), "importance": 4, "source_host": socket.gethostname(),
    }
    stable = json.dumps(observation, sort_keys=True, ensure_ascii=False)
    observation["idempotency_key"] = hashlib.sha256(stable.encode()).hexdigest()
    server = os.environ.get("MEMORY_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
    messages = payload.get("messages") or payload.get("transcript")
    if messages:
        chat_payload = {"project": project, "session_id": session_id, "source_host": socket.gethostname(), "messages": messages}
        try:
            chat_response = httpx.post(f"{server}/api/v1/chat/messages", json={"project": project, "session_id": session_id, "source_host": socket.gethostname(), "messages": messages}, timeout=5)
            chat_response.raise_for_status()
        except Exception:
            chat_payload["_queue_endpoint"] = "/api/v1/chat/messages"
            codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
            path = codex_home / "codex-memory" / "pending.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(chat_payload, ensure_ascii=False) + "\n")
    try:
        response = httpx.post(f"{server}/api/v1/observations", json=observation, timeout=3)
        response.raise_for_status()
    except Exception:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        path = codex_home / "codex-memory" / "pending.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(observation, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
