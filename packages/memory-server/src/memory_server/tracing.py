from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from .conversation import classify_tool_status
from .models import ChatMessage, Session


TOOL_KINDS: dict[str, dict[str, str]] = {
    "test": {"label": "测试", "symbol": "T"},
    "shell": {"label": "命令行", "symbol": ">_"},
    "file": {"label": "文件操作", "symbol": "F"},
    "search": {"label": "搜索", "symbol": "⌕"},
    "browser": {"label": "浏览器", "symbol": "◎"},
    "network": {"label": "网络 / MCP", "symbol": "↗"},
    "database": {"label": "数据库", "symbol": "D"},
    "code": {"label": "代码执行", "symbol": "{}"},
    "generic": {"label": "工具", "symbol": "◇"},
}

TOOL_KIND_RULES = [
    ("test", ("pytest", "unittest", "coverage", "jest", "vitest", " test")),
    ("browser", ("browser", "playwright", "chrome", "screenshot")),
    ("database", ("sqlite", "postgres", "database", "sql ", " db")),
    ("file", ("read_file", "write_file", "apply_patch", "filesystem", " file", "patch", "edit")),
    ("search", ("search", "grep", "find", "rg ", "ripgrep")),
    ("network", ("mcp", "http", "curl", "wget", "ssh", " api", "request", "fetch")),
    ("shell", ("shell", "terminal", "exec", "command", "bash", "powershell", "cmd.exe")),
    ("code", ("python", "node", "script", "code")),
]


def format_duration(milliseconds: float | int | None) -> str:
    if milliseconds is None:
        return "—"
    value = max(float(milliseconds), 0.0)
    if value < 1:
        return "<1 ms"
    if value < 1000:
        return f"{value:.0f} ms"
    seconds = value / 1000
    if seconds < 60:
        return f"{seconds:.2f}".rstrip("0").rstrip(".") + " s"
    minutes, remaining = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remaining:.0f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _delta_ms(later: datetime | None, earlier: datetime | None) -> float | None:
    if not later or not earlier:
        return None
    try:
        value = (later - earlier).total_seconds() * 1000
    except TypeError:
        return None
    return value if value >= 0 else None


def explicit_duration_ms(metadata: dict[str, Any]) -> float | None:
    for key in ("duration_ms", "elapsed_ms", "latency_ms", "execution_time_ms"):
        value = _number(metadata.get(key))
        if value is not None:
            return value
    for key in ("duration_seconds", "elapsed_seconds", "latency_seconds"):
        value = _number(metadata.get(key))
        if value is not None:
            return value * 1000
    started = _datetime(metadata.get("started_at"))
    ended = _datetime(metadata.get("ended_at"))
    return _delta_ms(ended, started)


def classify_tool_kind(metadata: dict[str, Any], tool_name: str | None = None) -> str:
    source = " ".join(
        str(value)
        for value in (
            tool_name,
            metadata.get("tool_name"),
            metadata.get("name"),
            metadata.get("tool"),
            metadata.get("command"),
            metadata.get("input"),
        )
        if value is not None
    ).lower()
    padded = f" {source} "
    for kind, needles in TOOL_KIND_RULES:
        if any(needle in padded for needle in needles):
            return kind
    return "generic"


def _event(message: ChatMessage, previous: ChatMessage | None) -> dict[str, Any]:
    metadata = message.metadata_ or {}
    exact_duration = explicit_duration_ms(metadata)
    estimated_gap = None if exact_duration is not None else _delta_ms(message.created_at, previous.created_at if previous else None)
    estimated_duration = estimated_gap if estimated_gap and estimated_gap > 0 else None
    duration = exact_duration if exact_duration is not None else estimated_duration
    tool_name = str(metadata.get("tool_name") or metadata.get("name") or metadata.get("tool") or "Tool execution")
    tool_kind = classify_tool_kind(metadata, tool_name) if message.role == "tool" else None
    labels = {"user": "用户请求", "assistant": "Codex 响应", "system": "系统上下文", "tool": tool_name}
    return {
        "id": message.id,
        "sequence": message.sequence,
        "role": message.role,
        "label": labels.get(message.role, message.role),
        "content": message.content,
        "snippet": " ".join(message.content.strip().split())[:220],
        "created_at": message.created_at,
        "duration_ms": duration,
        "duration_accuracy": "exact" if exact_duration is not None else ("estimated" if estimated_duration is not None else "none"),
        "tool_name": tool_name if message.role == "tool" else None,
        "tool_kind": tool_kind,
        "tool_kind_label": TOOL_KINDS[tool_kind]["label"] if tool_kind else None,
        "tool_symbol": TOOL_KINDS[tool_kind]["symbol"] if tool_kind else None,
        "tool_status": classify_tool_status(metadata, message.content) if message.role == "tool" else None,
        "metadata": metadata,
    }


def build_session_trace(session: Session, messages: Iterable[ChatMessage] | None = None) -> dict[str, Any]:
    ordered = sorted(messages if messages is not None else session.chat_messages, key=lambda item: (item.sequence, item.created_at))
    grouped: list[list[ChatMessage]] = []
    current: list[ChatMessage] = []
    for message in ordered:
        if message.role == "user" and current:
            grouped.append(current)
            current = []
        current.append(message)
    if current:
        grouped.append(current)

    paths: list[dict[str, Any]] = []
    request_number = 0
    for group_index, group in enumerate(grouped, 1):
        has_user = any(item.role == "user" for item in group)
        if has_user:
            request_number += 1
        events = [_event(message, group[index - 1] if index else None) for index, message in enumerate(group)]
        request = next((message for message in group if message.role == "user"), group[0])
        title = " ".join(request.content.strip().split()) or "会话上下文"
        observed_span = _delta_ms(group[-1].created_at, group[0].created_at) or 0
        exact_total = sum(event["duration_ms"] or 0 for event in events if event["duration_accuracy"] == "exact")
        total_duration = max(observed_span, exact_total) if observed_span or exact_total else None
        tool_events = [event for event in events if event["role"] == "tool"]
        paths.append({
            "number": request_number if has_user else None,
            "anchor": f"request-{request_number}" if has_user else f"context-{group_index}",
            "title": title[:140] + ("…" if len(title) > 140 else ""),
            "is_context": not has_user,
            "events": events,
            "event_count": len(events),
            "tool_count": len(tool_events),
            "failed_count": sum(event["tool_status"] == "failed" for event in tool_events),
            "duration_ms": total_duration,
            "duration_accuracy": "mixed" if exact_total else ("estimated" if observed_span else "none"),
            "started_at": group[0].created_at,
            "ended_at": group[-1].created_at,
        })

    request_paths = [path for path in paths if not path["is_context"]]
    tool_events = [event for path in paths for event in path["events"] if event["role"] == "tool"]
    wall_span = _delta_ms(ordered[-1].created_at, ordered[0].created_at) if ordered else None
    active_durations = [path["duration_ms"] for path in request_paths if path["duration_ms"] is not None]
    return {
        "session": session,
        "paths": paths,
        "request_count": len(request_paths),
        "message_count": len(ordered),
        "tool_count": len(tool_events),
        "failed_count": sum(event["tool_status"] == "failed" for event in tool_events),
        "active_duration_ms": sum(active_durations) if active_durations else None,
        "wall_span_ms": wall_span,
        "has_exact_timing": any(event["duration_accuracy"] == "exact" for path in paths for event in path["events"]),
    }
