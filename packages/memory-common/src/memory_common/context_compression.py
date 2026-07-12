"""Budget-aware, deterministic context compression for multi-turn agents.

The module deliberately has no model or tokenizer dependency.  It is intended as
the cheap first stage before an optional LLM summary or provider-native compact
operation.  Every decision is returned in ``CompressionReport`` so callers can
measure savings and reject unsafe policies in evaluation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


WORD_OR_CJK = re.compile(r"[A-Za-z0-9_./:-]+|[\u4e00-\u9fff]")
ERROR_LINE = re.compile(r"error|failed|fatal|exception|traceback|assert", re.IGNORECASE)
DECISION_LINE = re.compile(r"决定|约束|必须|不要|采用|decision|must|constraint|todo", re.IGNORECASE)


def estimate_tokens(text: str) -> int:
    """Return a conservative tokenizer-free estimate suitable for budgeting."""
    ascii_chars = sum(ord(char) < 128 for char in text)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)


@dataclass(slots=True)
class ContextMessage:
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0


@dataclass(slots=True)
class CompressionPolicy:
    max_input_tokens: int = 8_000
    reserved_output_tokens: int = 1_500
    recent_turns: int = 4
    min_recent_turns: int = 1
    tool_output_tokens: int = 600
    summary_tokens: int = 1_200
    safety_margin: float = 0.10

    @property
    def usable_tokens(self) -> int:
        gross = self.max_input_tokens - self.reserved_output_tokens
        return max(256, int(gross * (1 - self.safety_margin)))


@dataclass(slots=True)
class CompressionReport:
    strategy: str
    original_tokens: int
    compressed_tokens: int
    budget_tokens: int
    kept_messages: int
    summarized_messages: int
    dropped_messages: int
    protected_messages: int

    @property
    def reduction_ratio(self) -> float:
        if not self.original_tokens:
            return 0.0
        return round(1 - self.compressed_tokens / self.original_tokens, 4)


def _query_terms(query: str) -> set[str]:
    return {token.lower() for token in WORD_OR_CJK.findall(query) if len(token) > 1 or ord(token[0]) > 127}


def _relevance(content: str, query_terms: set[str]) -> float:
    terms = _query_terms(content)
    overlap = len(terms & query_terms) / max(len(query_terms), 1)
    decision_bonus = 0.25 if DECISION_LINE.search(content) else 0.0
    error_bonus = 0.20 if ERROR_LINE.search(content) else 0.0
    return overlap + decision_bonus + error_bonus


def _take_to_budget(text: str, token_budget: int) -> str:
    if estimate_tokens(text) <= token_budget:
        return text
    # Character slicing is conservative because CJK is estimated at one token.
    char_budget = max(32, token_budget * 3)
    return text[:char_budget].rstrip() + "\n…[truncated]"


def compress_tool_output(content: str, token_budget: int) -> str:
    """Keep error evidence plus head/tail, avoiding opaque tool-output deletion."""
    if estimate_tokens(content) <= token_budget:
        return content
    lines = content.splitlines()
    important = [line for line in lines if ERROR_LINE.search(line)][:12]
    candidates = lines[:12] + (["…[tool output compressed]…"] if len(lines) > 24 else []) + lines[-12:]
    for line in important:
        if line not in candidates:
            candidates.insert(12, line)
    return _take_to_budget("\n".join(candidates), token_budget)


def _turn_starts(messages: list[ContextMessage]) -> list[int]:
    return [index for index, message in enumerate(messages) if message.role == "user"]


def _recent_start(messages: list[ContextMessage], turns: int) -> int:
    starts = _turn_starts(messages)
    return starts[-turns] if len(starts) >= turns else 0


def _summary_entry(message: ContextMessage, query_terms: set[str]) -> str:
    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    ranked = sorted(
        enumerate(lines),
        key=lambda item: (_relevance(item[1], query_terms), -item[0]),
        reverse=True,
    )
    selected = sorted(index for index, line in ranked[:2] if _relevance(line, query_terms) > 0)
    if not selected and lines:
        selected = [0]
    excerpt = " ".join(lines[index] for index in selected)
    return _take_to_budget(f"- #{message.sequence} {message.role}: {excerpt}", 120)


def _turn_relevance(messages: list[ContextMessage], query_terms: set[str]) -> dict[int, float]:
    """Propagate relevance across a user-led turn so answers retain their question."""
    scores: dict[int, float] = {}
    turn: list[ContextMessage] = []
    for message in messages:
        if message.role == "user" and turn:
            score = max(_relevance(item.content, query_terms) for item in turn)
            scores.update({item.sequence: score for item in turn})
            turn = []
        turn.append(message)
    if turn:
        score = max(_relevance(item.content, query_terms) for item in turn)
        scores.update({item.sequence: score for item in turn})
    return scores


def compress_context(
    messages: Iterable[ContextMessage],
    current_query: str,
    policy: CompressionPolicy | None = None,
) -> tuple[list[ContextMessage], CompressionReport]:
    """Build a bounded context while preserving recent turns and hard evidence."""
    policy = policy or CompressionPolicy()
    source = list(messages)
    original_tokens = sum(estimate_tokens(item.content) for item in source)
    if original_tokens <= policy.usable_tokens:
        report = CompressionReport("passthrough", original_tokens, original_tokens, policy.usable_tokens, len(source), 0, 0, len([m for m in source if m.role == "system"]))
        return source, report

    query_terms = _query_terms(current_query)
    recent_start = _recent_start(source, policy.recent_turns)
    protected_indexes = {
        index for index, message in enumerate(source)
        if message.role == "system" or message.metadata.get("protected") is True
    }
    if source:
        protected_indexes.add(len(source) - 1)

    recent: list[ContextMessage] = []
    old: list[ContextMessage] = []
    for index, message in enumerate(source):
        content = compress_tool_output(message.content, policy.tool_output_tokens) if message.role == "tool" else message.content
        normalized = ContextMessage(message.role, content, dict(message.metadata), message.sequence)
        if index >= recent_start or index in protected_indexes:
            recent.append(normalized)
        else:
            old.append(normalized)

    protected_tokens = sum(estimate_tokens(item.content) for item in recent if item.role == "system" or item.metadata.get("protected"))
    recent_budget = max(256, policy.usable_tokens - protected_tokens - policy.summary_tokens)
    while sum(estimate_tokens(item.content) for item in recent) > recent_budget + protected_tokens:
        removable = next((i for i, item in enumerate(recent) if item.role != "system" and not item.metadata.get("protected") and item is not recent[-1]), None)
        if removable is None:
            break
        old.append(recent.pop(removable))

    turn_scores = _turn_relevance(source, query_terms)
    ranked_old = sorted(
        old,
        key=lambda item: (turn_scores.get(item.sequence, 0), _relevance(item.content, query_terms), item.sequence),
        reverse=True,
    )
    summary_lines: list[str] = []
    for message in ranked_old:
        entry = _summary_entry(message, query_terms)
        if estimate_tokens("\n".join(summary_lines + [entry])) > policy.summary_tokens:
            continue
        summary_lines.append(entry)

    result = list(recent)
    if summary_lines:
        result.insert(1 if result and result[0].role == "system" else 0, ContextMessage(
            role="system",
            content="[Compressed earlier context; sequence numbers refer to the archive]\n" + "\n".join(summary_lines),
            metadata={"generated": "extractive_summary", "protected": True},
            sequence=-1,
        ))

    # Final hard cap. Protected and current messages survive; generated summary shrinks first.
    compressed_tokens = sum(estimate_tokens(item.content) for item in result)
    if compressed_tokens > policy.usable_tokens:
        for item in result:
            if item.metadata.get("generated") == "extractive_summary":
                excess = compressed_tokens - policy.usable_tokens
                item.content = _take_to_budget(item.content, max(64, estimate_tokens(item.content) - excess))
                break
        compressed_tokens = sum(estimate_tokens(item.content) for item in result)

    kept_sequences = {item.sequence for item in result if item.sequence >= 0}
    summarized = len(old) if summary_lines else 0
    report = CompressionReport(
        strategy="hierarchical_extractive_v1",
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        budget_tokens=policy.usable_tokens,
        kept_messages=len(kept_sequences),
        summarized_messages=summarized,
        dropped_messages=max(0, len(source) - len(kept_sequences) - summarized),
        protected_messages=len(protected_indexes),
    )
    return result, report
