import re
from typing import Any, Iterable

from .models import ChatMessage

FENCE_PATTERN = re.compile(r"```([\w.+#-]*)[^\S\r\n]*\r?\n(.*?)(?:```|\Z)", re.DOTALL)
STRONG_TOOL_ERROR = re.compile(r"(?:^|\n)(?:FAILED|ERROR|FATAL|Traceback)\b|\b(?:AssertionError|TypeError|Exception):", re.IGNORECASE)
ROLE_LABELS = {"user": "用户", "assistant": "Codex", "system": "系统", "tool": "工具"}


def split_message_content(content: str) -> list[dict[str, str]]:
    """Split fenced code from prose; Jinja remains responsible for HTML escaping."""
    segments: list[dict[str, str]] = []
    cursor = 0
    for match in FENCE_PATTERN.finditer(content):
        if match.start() > cursor:
            text = content[cursor:match.start()]
            if text:
                segments.append({"kind": "text", "content": text})
        segments.append({"kind": "code", "content": match.group(2), "language": match.group(1) or "code"})
        cursor = match.end()
    if cursor < len(content):
        segments.append({"kind": "text", "content": content[cursor:]})
    return segments or [{"kind": "text", "content": content}]


def classify_tool_status(metadata: dict[str, Any], content: str) -> str:
    status = str(metadata.get("status", "")).lower()
    if metadata.get("is_error") is True:
        return "failed"
    failed_status = status in {"error", "failed", "failure", "fatal", "cancelled"}
    success_status = status in {"ok", "success", "succeeded", "completed", "passed"}
    normalized_exit_code: int | None = None
    exit_code = metadata.get("exit_code")
    if exit_code is not None:
        try:
            normalized_exit_code = int(exit_code)
        except (TypeError, ValueError):
            pass
    if failed_status or (normalized_exit_code is not None and normalized_exit_code != 0):
        return "failed"
    if success_status or normalized_exit_code == 0:
        return "success"
    if STRONG_TOOL_ERROR.search(content):
        return "failed"
    return "unknown"


def tool_failed(metadata: dict[str, Any], content: str) -> bool:
    return classify_tool_status(metadata, content) == "failed"


def message_view(message: ChatMessage) -> dict[str, Any]:
    metadata = message.metadata_ or {}
    segments = split_message_content(message.content)
    status = classify_tool_status(metadata, message.content) if message.role == "tool" else "unknown"
    failed = status == "failed"
    return {
        "id": message.id,
        "role": message.role,
        "role_label": ROLE_LABELS.get(message.role, message.role),
        "content": message.content,
        "segments": segments,
        "sequence": message.sequence,
        "created_at": message.created_at,
        "metadata": metadata,
        "tool_name": metadata.get("tool_name") or metadata.get("name") or metadata.get("tool") or "Tool execution",
        "tool_status": status,
        "content_length": len(message.content),
        "has_code": any(segment["kind"] == "code" for segment in segments),
        "has_error": failed,
    }


def _turn_title(messages: list[dict[str, Any]], fallback: str) -> str:
    user = next((message for message in messages if message["role"] == "user"), None)
    source = (user or messages[0])["content"] if messages else fallback
    title = " ".join(source.strip().split())
    return (title[:76] + "…") if len(title) > 76 else title or fallback


def build_chat_turns(messages: Iterable[ChatMessage]) -> list[dict[str, Any]]:
    """Group a chronological transcript into user-led turns with category tags."""
    grouped: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in sorted(messages, key=lambda item: (item.sequence, item.created_at)):
        view = message_view(message)
        if message.role == "user" and current:
            grouped.append(current)
            current = []
        current.append(view)
    if current:
        grouped.append(current)

    turns: list[dict[str, Any]] = []
    user_turn_number = 0
    for group_index, turn_messages in enumerate(grouped, 1):
        tags: list[dict[str, str]] = []
        if any(message["has_error"] for message in turn_messages):
            tags.append({"label": "异常", "tone": "error"})
        if any(message["role"] == "tool" for message in turn_messages):
            tags.append({"label": "工具", "tone": "tool"})
        if any(message["has_code"] for message in turn_messages):
            tags.append({"label": "代码", "tone": "code"})
        if not tags:
            tags.append({"label": "对话", "tone": "chat"})
        has_user = any(message["role"] == "user" for message in turn_messages)
        if has_user:
            user_turn_number += 1
        turns.append({
            "number": user_turn_number if has_user else None,
            "anchor": f"turn-{user_turn_number}" if has_user else f"context-{group_index}",
            "title": _turn_title(turn_messages, "会话上下文"),
            "messages": turn_messages,
            "tags": tags,
            "is_context": not has_user,
        })
    return turns
