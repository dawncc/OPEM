import json
import re
from collections.abc import Iterable


MAX_MEMORY_TITLE_CHARS = 40
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


def _normalized_result(content: str) -> str:
    result = content.partition("成功结果：")[2] or content.partition("原始输出：")[2]
    return result.replace('\\"', '"').replace("\\r", " ").replace("\\n", " ")


def _count(text: str, key: str) -> int | None:
    match = re.search(rf'["\']?{re.escape(key)}["\']?\s*[:=]\s*(\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _test_result(result: str) -> str | None:
    passed = re.search(r"(\d+) passed", result, re.IGNORECASE)
    failed = re.search(r"(\d+) failed", result, re.IGNORECASE)
    if passed and not failed:
        return f"运行测试，{passed.group(1)} 项用例全部通过"
    if passed or failed:
        parts = []
        if passed:
            parts.append(f"{passed.group(1)} 项通过")
        if failed:
            parts.append(f"{failed.group(1)} 项失败")
        return "运行测试：" + "、".join(parts)
    return None


def _shell_title(command: str, result: str) -> str:
    test_title = _test_result(result)
    if re.search(r"\bpytest\b|test[s]?\\|test[s]?/", command, re.IGNORECASE) and test_title:
        return test_title

    mentions_sync_in_search = re.search(
        r"(?:\brg\b|Select-String).{0,500}codex-memory-sync", command,
        re.IGNORECASE | re.DOTALL,
    )
    if "codex-memory-sync" in command and not mentions_sync_in_search:
        turns = _count(result, "completed_turns")
        messages = _count(result, "messages")
        errors = _count(result, "errors")
        mode = "预检历史同步" if "--dry-run" in command else "完成历史同步"
        facts = [f"{turns} 轮" if turns is not None else None, f"{messages} 条消息" if messages is not None else None]
        if errors is not None:
            facts.append(f"{errors} 错误")
        return f"{mode}：" + "/".join(item for item in facts if item)

    if "codex-memory-server" in command or "codex-memory-worker" in command:
        healthy = '"status":"ok"' in result.replace(" ", "") or "Health" in result and "ok" in result.lower()
        return "启动 Codex Memory 服务并通过健康检查" if healthy else "启动 Codex Memory Server 与 Worker"

    if re.search(r"codex(?:\.exe)?['\"]?\s+doctor\s+--help", command, re.IGNORECASE):
        return "确认 Codex doctor 可诊断配置、认证与运行健康"
    if re.search(r"codex(?:\.exe)?['\"]?\s+doctor\s+--json", command, re.IGNORECASE):
        if "background server is not running" in result:
            return "Codex 健康诊断发现后台服务未运行"
        return "运行 Codex 健康诊断并生成脱敏报告"
    if re.search(r"codex(?:\.exe)?['\"]?\s+--help", command, re.IGNORECASE):
        return "确认 Codex CLI 支持执行、评审与 MCP 管理"

    if "Get-Process" in command:
        return "检查 Codex 进程与 Hook 配置状态"
    if "sqlite3" in command.lower() or re.search(r"\bselect\b.+\bfrom\b", command, re.IGNORECASE | re.DOTALL):
        return "查询会话、消息与同步状态"
    if "Get-Content" in command and ("config.toml" in command or "hooks" in command.lower()):
        return "读取 Codex 配置并核对项目与 Hook 状态"
    if re.search(r"(?:^|[;|\s])(?:rg|Select-String)\s", command, re.IGNORECASE):
        quoted = re.search(r'(?:rg|Select-String)[^"\']*["\']([^"\']{3,80})["\']', command, re.IGNORECASE)
        topic = quoted.group(1).split("|")[0] if quoted else "相关实现"
        return f"检索 {topic} 并定位相关实现"
    if "git diff" in command or "git status" in command:
        return "检查代码差异与工作区状态"
    if "compileall" in command:
        return "编译检查 Python 代码并确认语法有效"
    if "Invoke-RestMethod" in command and "/health" in command:
        return "调用健康检查接口确认服务可用"

    executable = re.search(r"(?:^|[;&|]\s*)([\w.\\/-]+(?:\.exe)?)", command.strip())
    action = executable.group(1).split("\\")[-1].split("/")[-1] if executable else "命令"
    exit_code = re.search(r"Exit code:\s*(\d+)", result)
    return f"运行 {action} 并返回退出码 {exit_code.group(1)}" if exit_code else f"运行 {action} 并获取执行结果"


def _tool_memory_title(content: str, memory_type: str | None) -> str | None:
    success = re.search(r"工具执行成功：([^\n]+)", content)
    failure = re.search(r"FAQ：为什么工具\s+([^\n]+?)\s+执行失败", content)
    if not success and not failure and memory_type not in {"tool_success", "tool_failure_faq"}:
        return None
    tool_name = (success or failure).group(1).strip() if success or failure else "工具"
    result = _normalized_result(content)
    if failure or memory_type == "tool_failure_faq":
        reason = re.search(r"原因：([^\n]+)", content)
        return f"{tool_name} 执行失败：{reason.group(1).strip()}" if reason else f"{tool_name} 执行失败及处理建议"

    test_title = _test_result(result)
    if tool_name.casefold() == "wait":
        if test_title:
            return test_title
        if "background server is not running" in result:
            return "Codex 健康诊断发现后台服务未运行"
        if "Hooks |" in result and "persistent memories" in result:
            return "查阅 Codex Hooks 文档并确认持久记忆能力"
        return "异步任务完成并返回可用结果"

    input_match = re.search(r"调用输入：(.*?)(?:\n\n成功结果：|\n\n原始输出：|$)", content, re.DOTALL)
    tool_input = input_match.group(1).strip() if input_match else ""
    nested = re.search(r"tools\.([a-zA-Z0-9_]+)\s*\(", tool_input)
    operation = nested.group(1) if nested else tool_name

    if operation == "shell_command":
        return _shell_title(_field(tool_input, "command") or tool_input, result)
    if operation == "update_plan":
        explanation = _field(tool_input, "explanation")
        if explanation:
            return re.sub(r"^(已)?完成", "完成", explanation).rstrip("。")
        return "更新任务计划并同步执行进度"
    if operation == "apply_patch":
        files = re.findall(r"\*\*\* (?:Update|Add|Delete) File:\s*([^\r\n]+)", tool_input)
        if files:
            names = [item.replace("\\", "/").rsplit("/", 1)[-1] for item in files]
            return f"更新 {names[0]}" + (f" 等 {len(names)} 个文件" if len(names) > 1 else "")
        return "应用代码补丁并完成文件更新"
    if operation == "web__run":
        query = _field(tool_input, "q")
        sources = len(re.findall(r"https?://", result))
        return f"调研 {query} 并汇总外部资料" if query else f"读取并分析 {max(sources, 1)} 个外部资料来源"
    if operation in {"view_image", "image_gen__imagegen"}:
        return "检查并处理图像内容"
    if operation == "update_plan":
        return "更新任务计划并同步执行进度"
    return f"调用 {operation} 并提取有效结果"


def _knowledge_memory_title(content: str, memory_type: str | None) -> str | None:
    raw = re.sub(r"^类型：[^\n]+\s*", "", content.strip())
    conclusion_match = re.search(
        r"(?:^|\n)结论\s*\n(.*?)(?=\n\n(?:背景|背景与依据|原因|适用范围|待确认|原文依据)\s*\n|$)",
        raw,
        re.DOTALL,
    )
    conclusion = (conclusion_match.group(1) if conclusion_match else raw).strip()
    first_sentence = re.split(r"[。！？!?\n]", conclusion, maxsplit=1)[0].strip()
    verified = re.search(r"验证：[^。\n]*?(\d+)\s*项测试通过", conclusion)

    if memory_type == "solution":
        subject = first_sentence.split("：", 1)[0]
        subject = re.sub(r"的可靠性修复$|修复$", "", subject).strip()
        fix = re.search(r"修复为([^：；。\n]+)", conclusion)
        if fix:
            title = f"用{fix.group(1).strip()}修复{subject}"
            return f"{title}，{verified.group(1)} 项测试通过" if verified else title
    if memory_type == "problem" and first_sentence:
        return first_sentence if re.match(r"问题|失败|异常|无法|缺少", first_sentence) else f"发现问题：{first_sentence}"
    if memory_type == "decision" and first_sentence:
        return first_sentence if re.match(r"决定|采用|选择|使用", first_sentence) else f"决定：{first_sentence}"
    if first_sentence:
        return f"{first_sentence}，{verified.group(1)} 项测试通过" if verified else first_sentence
    return None


def _low_value(value: str) -> bool:
    text = re.sub(r"\s+", " ", value).strip().casefold()
    return bool(
        re.match(r"(?:工具执行成功|exec 成功执行|wait 执行成功|异步任务完成|调用 \w+ 并提取有效结果)", text)
        or "调用输入" in text
    )


def concise_memory_title(
    value: str | None,
    concepts: Iterable[str] | None = None,
    content: str | None = None,
    memory_type: str | None = None,
    max_chars: int = MAX_MEMORY_TITLE_CHARS,
) -> str:
    """Return a compact semantic title describing capability, action, and result."""
    original = re.sub(r"\s+", " ", (value or "").strip())
    needs_derivation = _low_value(original) or len(original) > min(max_chars, 32)
    derived = None
    tool_derived = None
    if needs_derivation:
        tool_derived = _tool_memory_title(content or value or "", memory_type)
        derived = tool_derived
        if not derived:
            derived = _knowledge_memory_title(content or value or "", memory_type)
    text = re.sub(r"\s+", " ", (derived or original).strip()).strip("#*-—–:：。；;，, ")
    text = re.split(r"[。！？!?\r\n]", text, maxsplit=1)[0].strip() or "未命名记忆"

    keywords: list[str] = []
    for item in concepts or []:
        keyword = re.sub(r"\s+", " ", str(item).strip()).strip("#*-—–:：。；;，, ")
        if (
            not keyword or keyword.casefold() in _GENERIC_CONCEPTS or len(keyword) > 10
            or keyword.casefold() in text[: max_chars // 2].casefold() or keyword in keywords
        ):
            continue
        keywords.append(keyword)
        if len(keywords) == 2:
            break
    prefix = f"{' / '.join(keywords)} · " if keywords and tool_derived is None else ""
    if len(prefix) >= max_chars - 8:
        prefix = ""
    available = max_chars - len(prefix)
    if len(text) > available:
        text = text[: max(available - 1, 1)].rstrip("，,；;：: ") + "…"
    return prefix + text
