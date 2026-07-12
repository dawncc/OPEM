"""Parse completed turns from Codex rollout JSONL files.

The rollout format is intentionally treated as a best-effort import source.
Real-time capture continues to use the stable public Hook payload instead.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


def _text(value: Any, limit: int = 490_000) -> str:
    if isinstance(value, str):
        result = value
    else:
        result = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    return result if len(result) <= limit else result[:limit] + "\n… [truncated during history sync]"


def _message_content(parts: Any) -> str:
    if isinstance(parts, str):
        return parts
    rendered: list[str] = []
    for part in parts or []:
        if not isinstance(part, dict):
            rendered.append(_text(part))
            continue
        if isinstance(part.get("text"), str):
            rendered.append(part["text"])
        elif part.get("type") in {"input_image", "image_url"}:
            rendered.append(f"[{part.get('type')}: {part.get('image_url') or part.get('url') or 'attached image'}]")
        else:
            rendered.append(_text(part))
    return "\n".join(item for item in rendered if item).strip()


def _turn_id(payload: dict[str, Any], current: str | None) -> str | None:
    metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
    return payload.get("turn_id") or metadata.get("turn_id") or current


def _tool_status(payload: dict[str, Any], call: dict[str, Any], output: Any) -> str:
    candidates = [payload, output if isinstance(output, dict) else {}, call]
    for candidate in candidates:
        status = str(candidate.get("status") or "").lower()
        if candidate.get("is_error") or candidate.get("success") is False:
            return "failed"
        if status in {"failed", "failure", "error", "cancelled", "timed_out", "timeout"}:
            return "failed"
        try:
            exit_code = candidate.get("exit_code", candidate.get("exitCode"))
            if exit_code is not None and int(exit_code) != 0:
                return "failed"
        except (TypeError, ValueError):
            pass
    return "completed"


@dataclass
class ParsedTurn:
    turn_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False
    summary: str | None = None
    duration_ms: float | None = None


@dataclass
class ParsedSession:
    session_id: str
    project: str
    cwd: str
    source_file: str
    messages: list[dict[str, Any]]
    completed_turns: int
    has_active_turn: bool
    summary: str | None


def parse_rollout(path: Path, project_override: str | None = None) -> ParsedSession | None:
    session_id: str | None = None
    cwd = ""
    current_turn: str | None = None
    turns: dict[str, ParsedTurn] = {}
    turn_order: list[str] = []
    calls: dict[str, dict[str, Any]] = {}
    lifecycle = "unknown"

    def get_turn(turn_id: str | None) -> ParsedTurn | None:
        if not turn_id:
            return None
        if turn_id not in turns:
            turns[turn_id] = ParsedTurn(turn_id)
            turn_order.append(turn_id)
        return turns[turn_id]

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            record_type = record.get("type")
            payload = record.get("payload") or {}
            timestamp = record.get("timestamp")
            if record_type == "session_meta":
                session_id = str(payload.get("id") or payload.get("session_id") or "") or session_id
                cwd = str(payload.get("cwd") or cwd)
                continue
            if record_type == "turn_context":
                current_turn = str(payload.get("turn_id") or current_turn or "") or None
                get_turn(current_turn)
                continue
            if record_type == "event_msg":
                event_type = payload.get("type")
                if event_type == "task_started":
                    lifecycle = "active"
                elif event_type == "task_complete":
                    turn = get_turn(_turn_id(payload, current_turn))
                    if turn:
                        turn.completed = True
                        turn.summary = payload.get("last_agent_message") or turn.summary
                        turn.duration_ms = payload.get("duration_ms")
                    lifecycle = "completed"
                continue
            if record_type != "response_item":
                continue

            item_type = payload.get("type")
            turn = get_turn(_turn_id(payload, current_turn))
            if not turn:
                continue
            if item_type == "message" and payload.get("role") in {"user", "assistant"}:
                role = payload["role"]
                content = _message_content(payload.get("content"))
                if not content:
                    continue
                message_id = str(payload.get("id") or "")
                event_id = f"message:{message_id}" if message_id else f"turn:{turn.turn_id}:{role}:{len(turn.messages)}"
                turn.messages.append({
                    "role": role, "content": content, "event_id": event_id,
                    "created_at": timestamp,
                    "metadata": {
                        "capture": "codex-history", "turn_id": turn.turn_id,
                        "phase": payload.get("phase"), "source_file": str(path),
                    },
                })
                continue
            if item_type in {"function_call", "custom_tool_call"}:
                call_id = str(payload.get("call_id") or payload.get("id") or "")
                if call_id:
                    calls[call_id] = {
                        "turn_id": turn.turn_id,
                        "tool_name": payload.get("name") or "tool",
                        "tool_input": payload.get("arguments", payload.get("input", {})),
                        "status": payload.get("status"),
                        "timestamp": timestamp,
                    }
                continue
            if item_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = str(payload.get("call_id") or "")
                call = calls.get(call_id, {})
                target = get_turn(str(call.get("turn_id") or turn.turn_id))
                if not target:
                    continue
                output = payload.get("output", payload.get("result", ""))
                content = _text(output)
                if not content:
                    content = "[tool completed without text output]"
                status = _tool_status(payload, call, output)
                target.messages.append({
                    "role": "tool", "content": content,
                    "event_id": f"tool:{call_id}" if call_id else f"tool-history:{len(target.messages)}",
                    "created_at": timestamp,
                    "metadata": {
                        "capture": "codex-history", "turn_id": target.turn_id,
                        "tool_name": call.get("tool_name") or "tool", "tool_use_id": call_id,
                        "status": status, "tool_input": _text(call.get("tool_input", {}), 100_000),
                        "source_file": str(path),
                    },
                })

    if not session_id:
        return None
    completed = [turns[item] for item in turn_order if turns[item].completed]
    if not completed:
        return None

    messages: list[dict[str, Any]] = []
    for turn in completed:
        # Some rollout versions echo the same user message around turn_context.
        # Keep the last occurrence so one historical turn still maps to one Hook event.
        seen_user: set[str] = set()
        deduplicated_reversed: list[dict[str, Any]] = []
        for message in reversed(turn.messages):
            if message["role"] == "user":
                if message["content"] in seen_user:
                    continue
                seen_user.add(message["content"])
            deduplicated_reversed.append(message)
        turn.messages = list(reversed(deduplicated_reversed))
        user_indexes = [index for index, message in enumerate(turn.messages) if message["role"] == "user"]
        assistant_indexes = [index for index, message in enumerate(turn.messages) if message["role"] == "assistant"]
        if user_indexes:
            turn.messages[user_indexes[-1]]["event_id"] = f"turn:{turn.turn_id}:user"
        if assistant_indexes:
            final = turn.messages[assistant_indexes[-1]]
            final["event_id"] = f"turn:{turn.turn_id}:assistant"
            if turn.duration_ms is not None:
                final["duration_ms"] = turn.duration_ms
        messages.extend(turn.messages)

    project = project_override or Path(cwd).name or "codex-history"
    return ParsedSession(
        session_id=session_id, project=project, cwd=cwd, source_file=str(path),
        messages=messages, completed_turns=len(completed),
        has_active_turn=lifecycle == "active",
        summary=next((turn.summary for turn in reversed(completed) if turn.summary), None),
    )


def discover_rollouts(codex_home: Path, since: str | None = None) -> Iterable[Path]:
    root = codex_home / "sessions"
    for path in sorted(root.glob("**/rollout-*.jsonl")):
        if since and path.name[8:18] < since:
            continue
        yield path
