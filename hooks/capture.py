"""Capture Codex lifecycle events without blocking the active turn.

The script intentionally uses only Python's standard library so it can run on
remote Codex hosts without installing the project package first.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def client_dir() -> Path:
    path = codex_home() / "codex-memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> dict[str, Any]:
    path = client_dir() / "client.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def acquire_lock(path: Path, timeout: float = 2.0) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 30:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.05)
    return None


def with_queue_lock(callback) -> Any:
    lock_path = client_dir() / "pending.lock"
    descriptor = acquire_lock(lock_path)
    if descriptor is None:
        raise TimeoutError("pending queue is busy")
    try:
        os.close(descriptor)
        return callback()
    finally:
        lock_path.unlink(missing_ok=True)


def append_pending(endpoint: str, payload: dict[str, Any]) -> None:
    def write() -> None:
        with (client_dir() / "pending.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"_queue_endpoint": endpoint, **payload}, ensure_ascii=False) + "\n")
    with_queue_lock(write)


def request_json(server: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{server.rstrip('/')}{endpoint}", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))


def flush_pending(server: str) -> int:
    def drain() -> int:
        path = client_dir() / "pending.jsonl"
        if not path.exists():
            return 0
        lines = path.read_text(encoding="utf-8").splitlines()
        remaining: list[str] = []
        sent = 0
        for index, line in enumerate(lines):
            try:
                payload = json.loads(line)
                endpoint = payload.pop("_queue_endpoint", "/api/v1/observations")
                request_json(server, endpoint, payload)
                sent += 1
            except Exception:
                remaining.extend(lines[index:])
                break
        temporary = path.with_suffix(".tmp")
        temporary.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
        temporary.replace(path)
        return sent
    try:
        return with_queue_lock(drain)
    except Exception:
        return 0


def deliver(server: str, endpoint: str, payload: dict[str, Any]) -> None:
    flush_pending(server)
    try:
        request_json(server, endpoint, payload)
    except Exception:
        append_pending(endpoint, payload)


def compact_json(value: Any, limit: int = 100_000) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    if len(text) > limit:
        return text[:limit] + "\n… [truncated by codex-memory capture]"
    return text


def project_name(payload: dict[str, Any], config: dict[str, Any]) -> str:
    if config.get("project"):
        return str(config["project"])
    cwd = Path(payload.get("cwd") or os.getcwd())
    return cwd.name or "codex"


def event_key(*parts: Any) -> str:
    raw = ":".join(str(part or "unknown") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def infer_tool_status(response: Any) -> str:
    if isinstance(response, dict):
        status = str(response.get("status", "")).lower()
        if response.get("is_error") or response.get("success") is False:
            return "failed"
        if status in {"failed", "failure", "error", "cancelled", "timed_out", "timeout"}:
            return "failed"
        try:
            if int(response.get("exit_code", response.get("exitCode", 0))) != 0:
                return "failed"
        except (TypeError, ValueError):
            pass
    return "completed"


def chat_payload(payload: dict[str, Any], config: dict[str, Any], message: dict[str, Any], *, status: str | None = None, summary: str | None = None) -> dict[str, Any]:
    result = {
        "project": project_name(payload, config),
        "session_id": str(payload.get("session_id") or payload.get("thread_id") or "unknown"),
        "source_host": socket.gethostname(),
        "messages": [message],
    }
    if status:
        result["session_status"] = status
    if summary:
        result["session_summary"] = summary
    return result


def handle(payload: dict[str, Any]) -> None:
    config = load_config()
    server = str(os.environ.get("MEMORY_SERVER_URL") or config.get("server") or "http://127.0.0.1:8000")
    event = str(payload.get("hook_event_name") or "")
    session_id = str(payload.get("session_id") or payload.get("thread_id") or "unknown")
    turn_id = str(payload.get("turn_id") or "unknown")
    common_metadata = {"capture": "codex-hook", "hook_event": event, "turn_id": turn_id}

    if event == "UserPromptSubmit" and payload.get("prompt"):
        message = {
            "role": "user", "content": str(payload["prompt"]),
            "event_id": f"turn:{turn_id}:user", "metadata": common_metadata,
        }
        deliver(server, "/api/v1/chat/messages", chat_payload(payload, config, message, status="active"))
        return

    if event == "PostToolUse":
        tool_name = str(payload.get("tool_name") or "tool")
        tool_response = payload.get("tool_response", "")
        metadata = {
            **common_metadata,
            "tool_name": tool_name,
            "tool_use_id": payload.get("tool_use_id"),
            "status": infer_tool_status(tool_response),
            "tool_input": compact_json(payload.get("tool_input", {})),
        }
        if isinstance(tool_response, dict):
            for key in ("exit_code", "exitCode", "duration_ms", "elapsed_ms"):
                if key in tool_response:
                    metadata[key] = tool_response[key]
        message = {
            "role": "tool", "content": compact_json(tool_response, 490_000),
            "event_id": f"tool:{payload.get('tool_use_id') or event_key(session_id, turn_id, tool_name, tool_response)}",
            "metadata": metadata,
        }
        deliver(server, "/api/v1/chat/messages", chat_payload(payload, config, message))
        return

    if event == "Stop" and payload.get("last_assistant_message"):
        assistant = str(payload["last_assistant_message"])
        message = {
            "role": "assistant", "content": assistant,
            "event_id": f"turn:{turn_id}:assistant", "metadata": common_metadata,
        }
        deliver(server, "/api/v1/chat/messages", chat_payload(payload, config, message, status="completed", summary=assistant))
        observation = {
            "content": assistant,
            "kind": "observation",
            "project": project_name(payload, config),
            "session_id": session_id,
            "concepts": ["codex-turn"],
            "files": [],
            "importance": 3,
            "source_host": socket.gethostname(),
            "idempotency_key": event_key("turn-observation", session_id, turn_id),
            "metadata": common_metadata,
        }
        deliver(server, "/api/v1/observations", observation)
        return

    if event == "PreCompact":
        # Every completed turn has already been captured by Stop. This durable
        # checkpoint records the boundary without depending on transcript format.
        observation = {
            "content": "Codex context was checkpointed before compaction; completed turns are stored separately.",
            "kind": "session_summary", "project": project_name(payload, config),
            "session_id": session_id, "concepts": ["pre-compact"], "files": [],
            "importance": 2, "source_host": socket.gethostname(),
            "idempotency_key": event_key("pre-compact", session_id, turn_id),
            "metadata": common_metadata,
        }
        deliver(server, "/api/v1/observations", observation)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        handle(payload)
    except Exception:
        # Memory capture must never interrupt Codex work. Failed deliveries are
        # queued whenever possible; malformed host events are simply ignored.
        pass
    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
