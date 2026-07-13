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
import uuid
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def client_dir() -> Path:
    path = codex_home() / "codex-memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def spool_dir() -> Path:
    path = client_dir() / "pending.d"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_diagnostic(name: str, payload: dict[str, Any]) -> None:
    """Best-effort diagnostics that can never break the active Codex turn."""
    path = client_dir() / name
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


def diagnostic_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    return {
        "hook_event_name": payload.get("hook_event_name"),
        "session_id": payload.get("session_id") or payload.get("thread_id"),
        "turn_id": payload.get("turn_id"),
        "payload_keys": sorted(str(key) for key in payload),
    }


def record_error(stage: str, exc: BaseException, payload: Any = None) -> None:
    write_diagnostic("capture-error.json", {
        "timestamp": time.time(),
        "stage": stage,
        "error_type": type(exc).__name__,
        "error": str(exc),
        **diagnostic_context(payload),
    })


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


def append_pending(endpoint: str, payload: dict[str, Any]) -> Path:
    """Persist one delivery atomically before attempting the network request."""
    queued = {"_queue_endpoint": endpoint, **payload}
    body = json.dumps(queued, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    destination = spool_dir() / f"{time.time_ns():020d}-{digest}.json"
    temporary = spool_dir() / f".{digest}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary.write_bytes(body)
    try:
        temporary.replace(destination)
    except OSError:
        if not destination.exists():
            raise
        temporary.unlink(missing_ok=True)
    return destination


def request_json(server: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{server.rstrip('/')}{endpoint}", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))


def migrate_legacy_pending() -> int:
    """Move the old shared JSONL queue into contention-free spool files."""
    def migrate() -> int:
        path = client_dir() / "pending.jsonl"
        if not path.exists():
            return 0
        lines = path.read_text(encoding="utf-8").splitlines()
        malformed: list[str] = []
        migrated = 0
        for line in lines:
            try:
                payload = json.loads(line)
                endpoint = payload.pop("_queue_endpoint", "/api/v1/observations")
                append_pending(endpoint, payload)
                migrated += 1
            except Exception as exc:
                malformed.append(line)
                record_error("migrate-legacy-pending", exc)
        temporary = path.with_suffix(".tmp")
        temporary.write_text("\n".join(malformed) + ("\n" if malformed else ""), encoding="utf-8")
        temporary.replace(path)
        return migrated
    try:
        return with_queue_lock(migrate)
    except Exception as exc:
        record_error("migrate-legacy-pending", exc)
        return 0


def send_queued(server: str, path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    endpoint = payload.pop("_queue_endpoint", "/api/v1/observations")
    request_json(server, endpoint, payload)
    path.unlink(missing_ok=True)


def flush_pending(server: str, time_budget: float = 6.0) -> int:
    migrate_legacy_pending()
    deadline = time.monotonic() + time_budget
    sent = 0
    for path in sorted(spool_dir().glob("*.json")):
        if time.monotonic() >= deadline:
            break
        queued_context: Any = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            queued_context = payload
            send_queued(server, path)
            sent += 1
        except Exception as exc:
            record_error("flush-pending", exc, queued_context)
            break
    return sent


def deliver(server: str, endpoint: str, payload: dict[str, Any]) -> None:
    # Write-ahead delivery prevents process termination, timeouts, or concurrent
    # hooks from losing an event between capture and the HTTP request.
    try:
        queued_path = append_pending(endpoint, payload)
    except Exception as exc:
        record_error("queue-delivery", exc, payload)
        # Disk failures should not prevent a best-effort direct delivery.
        request_json(server, endpoint, payload)
        return
    try:
        # Prioritize the current event so the live page is not delayed behind a
        # large offline backlog. The durable file remains if this request fails.
        send_queued(server, queued_path)
    except Exception as exc:
        record_error("deliver-current", exc, payload)
        return
    flush_pending(server)


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


def handle(payload: dict[str, Any]) -> bool:
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
        return True

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
        return True

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
        return True

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
        return True
    return False


def main() -> int:
    payload: Any = None
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise TypeError("hook payload must be a JSON object")
        handled = handle(payload)
        write_diagnostic("capture-status.json", {
            "timestamp": time.time(),
            "status": "handled" if handled else "ignored",
            "pending": len(list(spool_dir().glob("*.json"))),
            **diagnostic_context(payload),
        })
    except Exception as exc:
        # Memory capture must never interrupt Codex work. Failed deliveries are
        # queued whenever possible, while diagnostics preserve the root cause.
        record_error("main", exc, payload)
        write_diagnostic("capture-status.json", {
            "timestamp": time.time(), "status": "error",
            **diagnostic_context(payload),
        })
    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
