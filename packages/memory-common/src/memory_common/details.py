import json
import re
from collections.abc import Iterable

from .titles import concise_memory_title


OUTCOME_STATUS_LABELS = {
    "verified": "已验证",
    "observed": "已观察",
    "expected": "预期结果",
    "failed": "执行失败",
    "unknown": "待确认",
}

_GENERIC_CONCEPTS = {"tool-success", "tool-failure", "unknown", "codex-turn", "exec", "wait", "faq"}


def _field(text: str, key: str) -> str | None:
    match = re.search(rf'(?:["\']{re.escape(key)}["\']|\b{re.escape(key)})\s*:\s*', text)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[match.end():])
        return str(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _clip(value: str | None, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" #*-—：；;。")
    return text if len(text) <= limit else text[: limit - 1].rstrip("，,；;：: ") + "…"


def _result(content: str) -> str:
    value = content.partition("成功结果：")[2] or content.partition("原始输出：")[2]
    return value.replace('\\"', '"').replace("\\r", " ").replace("\\n", " ")


def _count(text: str, key: str) -> int | None:
    match = re.search(rf'["\']?{re.escape(key)}["\']?\s*[:=]\s*(\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _concept_subject(concepts: Iterable[str] | None) -> str | None:
    for concept in concepts or []:
        value = _clip(str(concept), 48)
        if value and value.casefold() not in _GENERIC_CONCEPTS:
            return value
    return None


def _tool_details(content: str, memory_type: str | None) -> dict[str, str] | None:
    tool = re.search(r"工具执行成功：([^\n]+)", content)
    failure = re.search(r"FAQ：为什么工具\s+([^\n]+?)\s+执行失败", content)
    if not tool and not failure and memory_type not in {"tool_success", "tool_failure_faq"}:
        return None

    tool_name = (tool or failure).group(1).strip() if tool or failure else "工具"
    input_match = re.search(r"调用输入：(.*?)(?:\n\n成功结果：|\n\n原始输出：|$)", content, re.DOTALL)
    tool_input = input_match.group(1).strip() if input_match else ""
    nested = re.search(r"tools\.([a-zA-Z0-9_]+)\s*\(", tool_input)
    operation = nested.group(1) if nested else tool_name
    result = _result(content)

    if failure or memory_type == "tool_failure_faq":
        reason = re.search(r"原因：([^\n]+)", content)
        return {
            "subject": _clip(operation, 80),
            "capability": _clip(f"执行 {operation} 并记录可复用的失败诊断"),
            "action": _clip(f"调用 {operation}"),
            "outcome": _clip(reason.group(1) if reason else "工具执行失败，原因需结合原始输出核对"),
            "outcome_status": "failed",
        }

    command = _field(tool_input, "command") or tool_input
    passed = re.search(r"(\d+) passed", result, re.IGNORECASE)
    failed = re.search(r"(\d+) failed", result, re.IGNORECASE)
    if operation == "shell_command" and re.search(r"\bpytest\b|test[s]?[\\/]", command, re.IGNORECASE):
        outcome_parts = []
        if passed:
            outcome_parts.append(f"{passed.group(1)} 项用例通过")
        if failed:
            outcome_parts.append(f"{failed.group(1)} 项用例失败")
        return {
            "subject": "自动化测试",
            "capability": "验证代码变更的行为与回归结果",
            "action": _clip(f"运行 {('pytest' if 'pytest' in command.casefold() else '测试命令')}"),
            "outcome": "，".join(outcome_parts) or "测试命令已返回结果",
            "outcome_status": "failed" if failed else ("verified" if passed else "observed"),
        }

    if operation == "shell_command" and "codex-memory-sync" in command:
        turns, messages, errors = (_count(result, key) for key in ("completed_turns", "messages", "errors"))
        facts = [
            f"{turns} 轮会话" if turns is not None else None,
            f"{messages} 条消息" if messages is not None else None,
            f"{errors} 个错误" if errors is not None else None,
        ]
        dry_run = "--dry-run" in command
        return {
            "subject": "Codex 历史记忆同步",
            "capability": "识别可同步的已完成会话与消息范围" if dry_run else "将历史会话与消息写入记忆服务",
            "action": "执行 codex-memory-sync 预检" if dry_run else "执行 codex-memory-sync 同步",
            "outcome": "，".join(item for item in facts if item) or "同步命令已返回统计结果",
            "outcome_status": "verified" if errors == 0 else "observed",
        }

    if operation == "shell_command" and re.search(r"codex(?:\.exe)?['\"]?\s+doctor", command, re.IGNORECASE):
        issue = "后台服务未运行" if "background server is not running" in result else "已生成运行健康诊断结果"
        return {
            "subject": "Codex 运行健康",
            "capability": "诊断配置、认证与后台服务状态",
            "action": "运行 codex doctor 健康检查",
            "outcome": issue,
            "outcome_status": "observed",
        }

    if operation == "update_plan":
        explanation = _field(tool_input, "explanation")
        return {
            "subject": "任务执行计划",
            "capability": "同步任务步骤、完成状态与后续动作",
            "action": "调用 update_plan 更新计划",
            "outcome": _clip(explanation or "计划状态已更新"),
            "outcome_status": "observed",
        }

    if operation == "web__run":
        query = _field(tool_input, "q")
        return {
            "subject": _clip(query or "外部资料调研", 80),
            "capability": "检索并汇总与问题直接相关的外部资料",
            "action": _clip(f"搜索 {query}" if query else "调用 web 检索并读取来源"),
            "outcome": "已返回可供核对的资料来源",
            "outcome_status": "observed",
        }

    readable_operation = operation.replace("__", ".")
    return {
        "subject": _clip(readable_operation, 80),
        "capability": _clip(f"调用 {readable_operation} 获取可复用的执行信息"),
        "action": _clip(f"调用 {readable_operation}"),
        "outcome": _clip(result or "工具已返回结果"),
        "outcome_status": "observed",
    }


def _knowledge_details(
    title: str,
    content: str,
    memory_type: str | None,
    concepts: Iterable[str] | None,
) -> dict[str, str]:
    semantic_title = concise_memory_title(title, concepts, content, memory_type)
    conclusion_match = re.search(
        r"(?:^|\n)结论\s*\n(.*?)(?=\n\n(?:背景|背景与依据|原因|适用范围|待确认|原文依据)\s*\n|$)",
        content,
        re.DOTALL,
    )
    raw_conclusion = (conclusion_match.group(1) if conclusion_match else content).strip()
    conclusion = _clip(raw_conclusion, 320)
    fix = re.search(r"修复为([^：；。\n]+)", raw_conclusion)
    verification = re.search(r"验证：([^。\n]+)", raw_conclusion)
    verified_tests = re.search(r"(\d+)\s*项测试通过", raw_conclusion)
    expected = re.search(r"(?:用于|以便|从而|保证|避免)([^。；\n]+)", raw_conclusion)

    subject = _concept_subject(concepts)
    solution_subject = re.search(r"用.+?修复(.+?)(?:，|$)", semantic_title)
    if solution_subject:
        subject = _clip(solution_subject.group(1), 80)
    elif memory_type == "solution":
        candidate = re.split(r"[：，]", semantic_title, maxsplit=1)[0]
        candidate = re.sub(r"的可靠性修复|可靠性修复|修复$", "", candidate).strip()
        subject = _clip(candidate, 80) or subject
    elif not subject:
        subject = _clip(re.split(r"[：，]", semantic_title, maxsplit=1)[0], 80)

    if memory_type == "solution":
        capability = _clip(f"通过{fix.group(1)}解决{subject}" if fix else f"解决{subject}并沉淀可复用方案")
        action = _clip(f"采用{fix.group(1)}" if fix else semantic_title)
    elif memory_type == "decision":
        capability = _clip(f"明确{subject}的选择、理由与适用范围")
        action = _clip(conclusion or semantic_title)
    elif memory_type == "problem":
        capability = _clip(f"识别{subject}的异常表现与影响")
        action = _clip("记录问题现象并保留诊断依据")
    elif memory_type in {"learning", "session_summary"}:
        capability = _clip(semantic_title)
        action = "提炼结论、依据与适用范围"
    else:
        capability = _clip(semantic_title)
        action = "整理可检索、可回溯的项目知识"

    if verification:
        outcome, status = verification.group(1), "verified"
    elif verified_tests:
        outcome, status = f"{verified_tests.group(1)} 项测试通过", "verified"
    elif expected:
        outcome, status = expected.group(1), "expected"
    else:
        outcome, status = "原始记录未提供独立验证结果", "unknown"
    return {
        "subject": _clip(subject or "当前记忆主题", 80),
        "capability": _clip(capability),
        "action": _clip(action),
        "outcome": _clip(outcome),
        "outcome_status": status,
    }


def derive_memory_details(
    title: str | None,
    content: str | None,
    memory_type: str | None = None,
    concepts: Iterable[str] | None = None,
) -> dict[str, str]:
    """Derive concise display fields without inventing facts beyond the memory content."""
    source = content or ""
    tool_details = _tool_details(source, memory_type)
    return tool_details or _knowledge_details(title or "", source, memory_type, concepts)


def outcome_status_label(value: str | None) -> str:
    return OUTCOME_STATUS_LABELS.get(value or "unknown", OUTCOME_STATUS_LABELS["unknown"])
