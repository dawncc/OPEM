import hashlib
import json
import re
from collections import Counter
from typing import Any, Iterable


TOOL_PROFILE_CACHE_KEY = "_tool_profile_v2"
TOOL_PROFILE_VERSION = "v2"
VOLATILE_KEYS = {
    "cursor", "offset", "page", "page_size", "limit", "timeout", "timeout_ms",
    "yield_time_ms", "max_tokens", "max_output_tokens", "request_id", "trace_id",
}
COMMAND_KEYS = ("command", "cmd", "query", "q", "path", "url")
IGNORED_OPERATION_KEYS = VOLATILE_KEYS | {
    "workdir", "cwd", "sandbox_permissions", "justification", "login", "prefix_rule",
}
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
LONG_HEX_RE = re.compile(r"\b[0-9a-f]{12,}\b", re.I)
ISO_TIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?\b", re.I)
NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.:/-]{2,}|[\u4e00-\u9fff]{2,}")
SHELL_WRAPPERS = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "bash", "sh", "zsh"}
NESTED_TOOL_RE = re.compile(r"\btools\.([a-zA-Z][\w]*)\s*\(")
JS_COMMAND_RE = re.compile(r'\bcommand\s*:\s*"((?:\\.|[^"\\])*)"', re.S)

FAMILY_LABELS = {
    "search": "搜索检索", "file": "文件处理", "testing": "测试验证", "version_control": "版本控制",
    "browser": "浏览器操作", "network": "网络请求", "database": "数据库", "memory": "记忆检索",
    "document": "文档数据", "image": "图像处理", "planning": "计划协作", "code": "代码执行",
    "shell": "命令执行", "generic": "其他工具",
}

FAMILY_RULES = (
    ("memory", ("memory_save", "memory_recall", "recall", "observation", "agentmemory", "codex_memory")),
    ("planning", ("update_plan", "request_user_input", "send_message", "followup_task", "spawn_agent", "wait_agent", "list_agents")),
    ("image", ("imagegen", "image_gen", "view_image", "screenshot")),
    ("version_control", ("git ", "git_", "git-", "github", "pull request", "commit", "branch")),
    ("testing", ("pytest", "unittest", "test runner", "npm test", "pnpm test", "cargo test", "go test")),
    ("search", ("search_query", "shell_command:rg", "shell_command:grep", "shell_command:find", "ripgrep", "search", "grep ", "rg ")),
    ("file", ("apply_patch", "get-content", "set-content", "read_file", "write_file", "remove-item", "move-item", "copy-item", "filesystem")),
    ("database", ("sqlite", "postgres", "psycopg", "sqlalchemy", "database", "select ", "insert ", "update ", "delete from")),
    ("browser", ("browser", "playwright", "chrome", "navigate", "domsnapshot", "open_tabs")),
    ("document", ("spreadsheet", "excel", "document", "docx", "pdf", "presentation", "pptx", "markdown")),
    ("network", ("web__run", "http://", "https://", "curl ", "invoke-webrequest", "request ")),
    ("code", ("python", "node_repl", "javascript", "typescript", "compile", "py_compile", "npm ", "pnpm ")),
    ("shell", ("shell_command", "powershell", "bash", "command", "exec")),
)

OPERATION_FAMILIES = {
    "rg": "search", "grep": "search", "find": "search", "search_query": "search",
    "pytest": "testing", "unittest": "testing", "git": "version_control",
    "get-content": "file", "get-childitem": "file", "set-content": "file", "apply_patch": "file",
    "curl": "network", "invoke-webrequest": "network", "open": "browser", "screenshot": "browser",
    "python": "code", "python.exe": "code", "node": "code", "node.exe": "code",
}

STOPWORDS = {
    "the", "and", "for", "from", "with", "this", "that", "into", "true", "false", "none", "null",
    "const", "await", "result", "output", "input", "text", "content", "command", "tools", "tool", "call",
    "workdir", "timeout_ms", "ensure_ascii", "sort_keys", "completed", "success", "failed", "error", "info",
    "http", "https", "localhost", "127.0.0.1", "script", "line", "file", "path", "value", "return",
    "work", "project", "codex", "codex_work", "codex-lan-memory", "demo", "packages", "server",
    "使用", "调用", "工具", "结果", "输出", "输入", "执行", "当前", "查看", "完成", "成功", "失败", "信息",
}

LANGUAGE_PATTERNS = (
    ("Python", re.compile(r"(?:\.py\b|\bpython\b|\bpytest\b|traceback \(most recent call last\)|\bpip\b)", re.I)),
    ("TypeScript", re.compile(r"(?:\.tsx?\b|\btypescript\b|\btsc\b)", re.I)),
    ("JavaScript", re.compile(r"(?:\.jsx?\b|\bjavascript\b|\bnode(?:\.js)?\b|\bnpm\b|\bpnpm\b)", re.I)),
    ("SQL", re.compile(r"(?:\.sql\b|\bsqlite\b|\bpostgres(?:ql)?\b|\bselect\s+.+\s+from\b|\binsert\s+into\b)", re.I | re.S)),
    ("PowerShell", re.compile(r"(?:\.ps1\b|\bpowershell\b|\bpwsh\b|\$env:|\bget-content\b|\bget-childitem\b)", re.I)),
    ("Shell", re.compile(r"(?:\.sh\b|#!/bin/(?:ba)?sh|\bbash\b|\bzsh\b|shell_command)", re.I)),
    ("HTML", re.compile(r"(?:\.html?\b|<!doctype html|<html\b)", re.I)),
    ("CSS", re.compile(r"(?:\.css\b|\bdisplay\s*:\s*(?:grid|flex)|@media\s*\()", re.I)),
    ("JSON", re.compile(r"(?:\.json\b|\bjson\.loads\b|\bjson\.dumps\b)", re.I)),
    ("YAML", re.compile(r"(?:\.ya?ml\b|docker-compose)", re.I)),
    ("Markdown", re.compile(r"(?:\.md\b|```(?:markdown|md))", re.I)),
    ("Rust", re.compile(r"(?:\.rs\b|\bcargo\b|\brustc\b)", re.I)),
    ("Go", re.compile(r"(?:\.go\b|\bgo\s+(?:test|run|build)\b)", re.I)),
)


def _normalized_json(value: Any, key: str | None = None) -> Any:
    if key and key.casefold() in VOLATILE_KEYS:
        return "<value>"
    if isinstance(value, dict):
        return {str(item_key).casefold(): _normalized_json(item, str(item_key)) for item_key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalized_json(item) for item in value]
    if isinstance(value, str):
        return normalize_tool_input(value)
    return "<value>" if isinstance(value, (int, float)) else value


def normalize_tool_input(value: str | None) -> str:
    if not value:
        return ""
    stripped = value.strip()
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        return json.dumps(_normalized_json(parsed), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    normalized = stripped.casefold()
    normalized = UUID_RE.sub("<id>", normalized)
    normalized = LONG_HEX_RE.sub("<id>", normalized)
    normalized = ISO_TIME_RE.sub("<date>", normalized)
    normalized = NUMBER_RE.sub("<n>", normalized)
    return SPACE_RE.sub(" ", normalized)


def canonical_tool_name(name: str) -> str:
    return SPACE_RE.sub(" ", name.strip().casefold()) or "tool"


def _plain_command_operation(command: str) -> str:
    tokens = re.findall(r'[^\s"\']+', command.casefold())
    if not tokens:
        return "call"
    index = 0
    if tokens[0] in SHELL_WRAPPERS:
        index = 1
        while index < len(tokens) and tokens[index].startswith("-"):
            index += 1
    token = tokens[index] if index < len(tokens) else tokens[0]
    token = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return token.rstrip(";,") or "call"


def _command_operation(command: str) -> str:
    nested_tool = NESTED_TOOL_RE.search(command)
    if nested_tool:
        operation = nested_tool.group(1).casefold()
        if operation == "shell_command":
            command_match = JS_COMMAND_RE.search(command, nested_tool.end())
            if command_match:
                try:
                    inner_command = json.loads(f'"{command_match.group(1)}"')
                except ValueError:
                    inner_command = command_match.group(1)
                return f"{operation}:{_plain_command_operation(inner_command)}"
        return operation
    return _plain_command_operation(command)


def tool_operation(input_text: str | None) -> str:
    if not input_text or not input_text.strip():
        return "call"
    try:
        parsed = json.loads(input_text)
    except (TypeError, ValueError):
        return _command_operation(input_text)
    if isinstance(parsed, dict):
        for key in COMMAND_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return _command_operation(value) if key in {"command", "cmd"} else key
        meaningful = sorted(str(key).casefold() for key in parsed if str(key).casefold() not in IGNORED_OPERATION_KEYS)
        return meaningful[0] if meaningful else "call"
    return "call"


def execution_input(execution: Any) -> str | None:
    if getattr(execution, "input_text", None):
        return execution.input_text
    metadata = getattr(execution, "metadata_", {}) or {}
    value = (
        metadata.get("input") or metadata.get("tool_input") or metadata.get("command")
        or metadata.get("args") or metadata.get("arguments") or metadata.get("tool_args")
        or metadata.get("tool_arguments")
    )
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def extract_keywords(input_text: str | None, output_text: str | None, limit: int = 8) -> list[str]:
    scores: Counter[str] = Counter()
    for text, weight in ((input_text or "", 3), (output_text or "", 1)):
        for raw in WORD_RE.findall(text[:12000]):
            token = raw.casefold().strip("._:/-")
            if not token or token in STOPWORDS or len(token) > 48 or LONG_HEX_RE.fullmatch(token):
                continue
            if "\\" in token or "/" in token:
                token = re.split(r"[/\\]", token)[-1]
            if len(token) < 2 or token in STOPWORDS:
                continue
            scores[token] += weight
            if "_" in token:
                for part in token.split("_"):
                    if len(part) >= 3 and part not in STOPWORDS:
                        scores[part] += max(1, weight - 1)
    return [token for token, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def detect_languages(input_text: str | None, output_text: str | None, operation: str = "") -> list[str]:
    scores: dict[str, int] = {}
    for language, pattern in LANGUAGE_PATTERNS:
        score = 0
        if pattern.search(operation):
            score += 5
        if pattern.search((input_text or "")[:12000]):
            score += 3
        if pattern.search((output_text or "")[:12000]):
            score += 1
        if score:
            scores[language] = score
    return [language for language, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:3]]


def classify_tool_family(tool_name: str, operation: str, input_text: str | None, output_text: str | None) -> str:
    operation_leaf = operation.casefold().split(":")[-1]
    if operation_leaf in OPERATION_FAMILIES:
        return OPERATION_FAMILIES[operation_leaf]
    primary = f"{tool_name.casefold()} {operation.casefold()}"
    for family, needles in FAMILY_RULES:
        if any(needle in primary for needle in needles):
            return family
    return "generic"


def normalized_action(family: str, operation: str) -> str:
    lowered = operation.casefold()
    if family == "search":
        return "search"
    if family == "testing":
        return "test"
    if family == "version_control":
        return "version-control"
    if family == "browser":
        return "browse"
    if family == "network":
        return "request"
    if family == "database":
        return "query"
    if family == "memory":
        return "remember" if "save" in lowered or "submit" in lowered else "recall"
    if family == "planning":
        return "collaborate" if any(item in lowered for item in ("message", "agent", "followup")) else "plan"
    if family == "image":
        return "generate" if "gen" in lowered else "inspect"
    if family == "document":
        return "document"
    if family == "file":
        return "edit" if any(item in lowered for item in ("patch", "write", "set-", "remove", "move", "copy")) else "read"
    if family == "code":
        return "run-code"
    return lowered or "call"


def build_tool_profile(tool_name: str, input_text: str | None, output_text: str | None) -> dict[str, Any]:
    output_text = output_text or ""
    operation = tool_operation(input_text)
    family = classify_tool_family(tool_name, operation, input_text, output_text)
    languages = detect_languages(input_text, output_text, operation)
    keywords = extract_keywords(input_text, output_text)
    action = normalized_action(family, operation)
    return {
        "version": TOOL_PROFILE_VERSION,
        "tool_name": tool_name,
        "canonical_tool": canonical_tool_name(tool_name),
        "operation": operation,
        "family": family,
        "family_label": FAMILY_LABELS[family],
        "action": action,
        "keywords": keywords,
        "languages": languages,
        "tags": [FAMILY_LABELS[family], action, *languages, *keywords[:5]],
    }


def metadata_with_tool_profile(
    metadata: dict[str, Any] | None, tool_name: str,
    input_text: str | None, output_text: str | None,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated[TOOL_PROFILE_CACHE_KEY] = build_tool_profile(tool_name, input_text, output_text)
    return updated


def profile_tool_execution(execution: Any) -> dict[str, Any]:
    metadata = getattr(execution, "metadata_", {}) or {}
    cached = metadata.get(TOOL_PROFILE_CACHE_KEY)
    if not isinstance(cached, dict) or cached.get("version") != TOOL_PROFILE_VERSION:
        cached = build_tool_profile(
            execution.tool_name, execution_input(execution), getattr(execution, "output_text", "") or "",
        )
    profile = dict(cached)
    profile["execution"] = execution
    profile["_keyword_set"] = set(profile["keywords"])
    profile["_language_set"] = set(profile["languages"])
    return profile


def profile_similarity(first: dict[str, Any], second: dict[str, Any]) -> float:
    if first["family"] != second["family"]:
        return 0.0
    score = 0.35
    if first["action"] == second["action"]:
        score += 0.30
    if first["canonical_tool"] == second["canonical_tool"]:
        score += 0.05
    first_keywords = first.get("_keyword_set") or set(first["keywords"])
    second_keywords = second.get("_keyword_set") or set(second["keywords"])
    if first_keywords or second_keywords:
        score += 0.20 * len(first_keywords & second_keywords) / len(first_keywords | second_keywords)
    first_languages = first.get("_language_set") or set(first["languages"])
    second_languages = second.get("_language_set") or set(second["languages"])
    if first_languages or second_languages:
        score += 0.10 * len(first_languages & second_languages) / len(first_languages | second_languages)
    return round(min(score, 1.0), 4)


def group_tool_profiles(profiles: Iterable[dict[str, Any]], similarity_threshold: float = 0.62) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    clusters_by_signature: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for profile in sorted(profiles, key=lambda item: item["execution"].created_at, reverse=True):
        best_cluster = None
        best_score = 0.0
        signature = (profile["family"], profile["action"])
        candidate_clusters = clusters_by_signature.setdefault(signature, [])
        for cluster in candidate_clusters:
            representative = cluster["profiles"][0]
            score = profile_similarity(profile, representative)
            threshold = 0.72 if profile["family"] in {"shell", "generic"} else similarity_threshold
            if score >= threshold and score > best_score:
                best_cluster, best_score = cluster, score
        if best_cluster is None:
            cluster = {"profiles": [profile], "scores": [1.0]}
            clusters.append(cluster)
            candidate_clusters.append(cluster)
        else:
            best_cluster["profiles"].append(profile)
            best_cluster["scores"].append(best_score)

    result: list[dict[str, Any]] = []
    for cluster in clusters:
        items = cluster["profiles"]
        executions = [item["execution"] for item in items]
        statuses = Counter(item.status for item in executions)
        keyword_counts = Counter(keyword for item in items for keyword in item["keywords"])
        actions = Counter(item["action"] for item in items)
        family = items[0]["family"]
        signature = f"{family}|{','.join(sorted({item['canonical_tool'] for item in items}))}|{actions.most_common(1)[0][0]}"
        result.append({
            "id": hashlib.sha256(signature.encode()).hexdigest()[:12],
            "family": family,
            "family_label": FAMILY_LABELS[family],
            "action": actions.most_common(1)[0][0],
            "actions": [name for name, _ in actions.most_common()],
            "tool_names": sorted({item["tool_name"] for item in items}, key=str.casefold),
            "keywords": [name for name, _ in keyword_counts.most_common(8)],
            "languages": sorted({language for item in items for language in item["languages"]}),
            "profiles": items,
            "executions": executions,
            "count": len(executions),
            "success_count": statuses["success"],
            "failed_count": statuses["failed"],
            "unknown_count": statuses["unknown"],
            "projects": sorted({item.project.name for item in executions}),
            "first_seen": executions[-1].created_at,
            "last_seen": executions[0].created_at,
            "similarity": round(sum(cluster["scores"]) * 100 / len(cluster["scores"]), 1),
        })
    return sorted(result, key=lambda group: (group["count"], group["last_seen"]), reverse=True)


def group_tool_executions(executions: Iterable[Any], similarity_threshold: float = 0.62) -> list[dict[str, Any]]:
    return group_tool_profiles((profile_tool_execution(item) for item in executions), similarity_threshold)


def tool_group_facets(profiles: Iterable[dict[str, Any]], keyword_limit: int = 30) -> dict[str, list[dict[str, Any]]]:
    items = list(profiles)
    family_counts = Counter(item["family"] for item in items)
    language_counts = Counter(language for item in items for language in item["languages"])
    keyword_counts = Counter(keyword for item in items for keyword in item["keywords"])
    return {
        "families": [
            {"value": family, "label": FAMILY_LABELS[family], "count": count}
            for family, count in sorted(family_counts.items(), key=lambda item: (-item[1], FAMILY_LABELS[item[0]]))
        ],
        "languages": [{"value": name, "label": name, "count": count} for name, count in language_counts.most_common()],
        "keywords": [{"value": name, "label": name, "count": count} for name, count in keyword_counts.most_common(keyword_limit)],
    }


def filter_tool_profiles(
    profiles: Iterable[dict[str, Any]], family: str | None = None,
    language: str | None = None, keyword: str | None = None,
) -> list[dict[str, Any]]:
    return [
        item for item in profiles
        if (not family or item["family"] == family)
        and (not language or language in item["languages"])
        and (not keyword or keyword in item["keywords"])
    ]


def summarize_tool_groups(groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(groups)
    total = sum(group["count"] for group in items)
    success = sum(group["success_count"] for group in items)
    failed = sum(group["failed_count"] for group in items)
    unknown = sum(group["unknown_count"] for group in items)
    projects = {project for group in items for project in group["projects"]}
    return {
        "groups": len(items), "calls": total, "success": success, "failed": failed, "unknown": unknown,
        "success_rate": round(success * 100 / total, 1) if total else 0,
        "repeated_groups": sum(group["count"] > 1 for group in items), "projects": len(projects),
    }
