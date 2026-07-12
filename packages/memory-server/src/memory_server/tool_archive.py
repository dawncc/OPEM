import json
import re
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

from .conversation import classify_tool_status
from .models import ChatMessage, Observation, ProcessingJob, Project, Session, ToolExecution

ERROR_LINE = re.compile(r"(?:AssertionError|TypeError|ValueError|RuntimeError|PermissionError|FileNotFoundError|TimeoutError|ConnectionError|Exception|ERROR|FAILED|FATAL|Traceback)", re.IGNORECASE)


def stringify_input(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("input") or metadata.get("command") or metadata.get("args") or metadata.get("arguments")
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def extract_failure_reason(output: str, metadata: dict[str, Any]) -> str:
    explicit = metadata.get("error_message") or metadata.get("error") or metadata.get("reason")
    if explicit:
        return str(explicit).strip()[:1000]
    evidence_text = str(metadata.get("stderr") or output)
    lines = [line.strip() for line in evidence_text.splitlines() if line.strip()]
    for line in reversed(lines):
        if ERROR_LINE.search(line):
            return line[:1000]
    return (lines[-1][:1000] if lines else "工具返回失败状态，但没有提供明确错误信息。")


def failure_suggestion(reason: str) -> str:
    lowered = reason.lower()
    if "unexpected keyword" in lowered or "typeerror" in lowered:
        return "检查函数或测试替身的参数签名，使调用参数与实现保持一致，然后重新运行。"
    if "not found" in lowered or "filenotfound" in lowered or "command not found" in lowered:
        return "确认依赖已经安装，并检查命令、工作目录和文件路径是否正确。"
    if "permission" in lowered or "denied" in lowered:
        return "检查目标文件、目录或服务的权限，使用最小必要权限后重试。"
    if "unauthorized" in lowered or "forbidden" in lowered or "authentication" in lowered:
        return "检查凭据、登录状态和目标服务的认证配置，更新有效凭据后重试。"
    if "timeout" in lowered or "timed out" in lowered:
        return "检查网络或服务健康状态，确认超时设置合理，并使用退避策略重试。"
    if "connection" in lowered or "refused" in lowered:
        return "确认目标服务正在监听、地址和端口正确，并检查网络路由或隧道。"
    if "assert" in lowered:
        return "对照失败断言检查实际输出、测试前置条件和最近改动，修复后单独重跑失败用例。"
    return "从最后一条错误开始检查输入、环境和依赖，修复根因后以相同参数重试并记录结果。"


def error_category(reason: str) -> str:
    lowered = reason.lower()
    categories = [
        ("type_error", ("typeerror", "unexpected keyword")),
        ("authentication", ("unauthorized", "forbidden", "authentication")),
        ("permission", ("permission", "denied")),
        ("not_found", ("not found", "filenotfound", "command not found")),
        ("timeout", ("timeout", "timed out")),
        ("network", ("connection", "refused", "network")),
        ("assertion", ("assertionerror", "assertion failed", "assert ")),
        ("cancelled", ("cancelled", "canceled")),
        ("process_exit", ("command failed", "process failed", "exit code", "non-zero", "nonzero")),
    ]
    return next((category for category, needles in categories if any(needle in lowered for needle in needles)), "unknown")


def failure_signature(tool_name: str, category: str, reason: str) -> str:
    normalized = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", reason.lower())
    normalized = re.sub(r"(?:[a-zA-Z]:)?[/\\][^\s:]+", "<path>", normalized)
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    return hashlib.sha256(f"{tool_name.lower()}|{category}|{normalized}".encode()).hexdigest()[:32]


def build_faq_answer(reason: str, category: str, suggestion: str) -> str:
    return f"已观察到的证据：{reason}\n错误分类：{category}\n处理建议：{suggestion}"


def _memory_content(tool_name: str, status: str, input_text: str | None, output: str, reason: str | None, answer: str | None) -> str:
    clipped_output = output if len(output) <= 3000 else output[:3000].rstrip() + "…"
    if status == "failed":
        return (
            f"FAQ：为什么工具 {tool_name} 执行失败？\n\n"
            f"原因：{reason}\n\n处理建议：{answer}\n\n"
            f"调用输入：{input_text or '未记录'}\n\n原始输出：\n{clipped_output}"
        )
    return (
        f"工具执行成功：{tool_name}\n\n"
        f"调用输入：{input_text or '未记录'}\n\n成功结果：\n{clipped_output}\n\n"
        "该记录可作为后续执行相同工具时的历史上下文；使用前仍需核对当前环境和参数。"
    )


def archive_tool_message(db: DbSession, project: Project, session: Session, message: ChatMessage) -> ToolExecution:
    existing = db.scalar(select(ToolExecution).where(ToolExecution.chat_message_id == message.id))
    if existing:
        return existing
    metadata = message.metadata_ or {}
    name = str(metadata.get("tool_name") or metadata.get("name") or metadata.get("tool") or "Tool execution")[:200]
    status = classify_tool_status(metadata, message.content)
    failed = status == "failed"
    input_text = stringify_input(metadata)
    reason = extract_failure_reason(message.content, metadata) if failed else None
    suggestion = failure_suggestion(reason) if reason else None
    category = error_category(reason) if reason else None
    signature = failure_signature(name, category, reason) if reason and category else None
    question = f"为什么 {name} 工具执行失败？" if failed else None
    answer = build_faq_answer(reason, category, suggestion) if failed else None
    archive = ToolExecution(
        project_id=project.id, session_id=session.id, chat_message_id=message.id,
        tool_name=name, status=status, input_text=input_text, output_text=message.content,
        error_reason=reason, error_category=category, evidence=reason, failure_signature=signature,
        faq_question=question, faq_answer=answer, derivation_version="v1",
        metadata_=metadata, created_at=message.created_at,
    )
    db.add(archive)
    db.flush()
    should_derive = failed or (status == "success" and bool(message.content.strip()))
    if not should_derive:
        return archive
    observation = Observation(
        project_id=project.id, session_id=session.id,
        kind="problem" if failed else "learning",
        content=_memory_content(name, status, input_text, message.content, reason, suggestion),
        concepts=["FAQ", "tool-failure", category or "unknown", signature or "", name] if failed else ["tool-success", name],
        files=[], importance=4 if failed else 3,
        metadata_={"tool_execution_id": str(archive.id), "tool_name": name, "tool_status": status},
        idempotency_key=f"tool-execution:{message.id}:v1",
    )
    db.add(observation)
    db.flush()
    archive.observation_id = observation.id
    db.add(ProcessingJob(observation_id=observation.id))
    return archive


def backfill_tool_archives(db: DbSession) -> int:
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.role == "tool", ~ChatMessage.tool_archive.has())
        .options(selectinload(ChatMessage.session).selectinload(Session.project))
        .order_by(ChatMessage.created_at)
    ).all()
    for message in messages:
        archive_tool_message(db, message.session.project, message.session, message)
    db.commit()
    return len(messages)
