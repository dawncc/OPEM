"""Offline replay benchmark for multi-turn context compression policies."""

from __future__ import annotations

import json

from memory_common.context_compression import CompressionPolicy, ContextMessage, compress_context, estimate_tokens


def fixture() -> list[ContextMessage]:
    messages = [ContextMessage("system", "Respect repository rules. Never print credentials.", sequence=0)]
    facts = [
        ("database", "Decision: PostgreSQL port is 55432 and must not change."),
        ("api", "Decision: public API prefix is /api/v1."),
        ("retry", "Constraint: retry failed worker jobs at most three times."),
        ("deploy", "Decision: deployment target is the LAN host 192.168.1.20."),
    ]
    sequence = 1
    for topic, fact in facts:
        messages.append(ContextMessage("user", f"Plan the {topic} work. " + "background " * 220, sequence=sequence)); sequence += 1
        messages.append(ContextMessage("assistant", fact + " explanation " * 180, sequence=sequence)); sequence += 1
        messages.append(ContextMessage("tool", "\n".join(f"build log line {i}" for i in range(250)), sequence=sequence)); sequence += 1
    messages.append(ContextMessage("user", "What database port did we decide on?", sequence=sequence))
    return messages


def keyword_recall(text: str, expected: list[str]) -> float:
    return sum(value in text for value in expected) / len(expected)


def run() -> dict:
    messages = fixture()
    query = messages[-1].content
    expected = ["55432", "Never print credentials", "database"]
    original = "\n".join(item.content for item in messages)
    rows = [{
        "strategy": "full_history",
        "tokens": estimate_tokens(original),
        "reduction": 0.0,
        "critical_fact_recall": keyword_recall(original, expected),
    }]
    for budget in (6000, 3000, 1500):
        compressed, report = compress_context(messages, query, CompressionPolicy(
            max_input_tokens=budget + 500,
            reserved_output_tokens=500,
            recent_turns=2,
            summary_tokens=max(250, budget // 4),
            tool_output_tokens=max(100, budget // 10),
            safety_margin=0,
        ))
        text = "\n".join(item.content for item in compressed)
        rows.append({
            "strategy": f"hierarchical_{budget}",
            "tokens": report.compressed_tokens,
            "reduction": report.reduction_ratio,
            "critical_fact_recall": keyword_recall(text, expected),
            "within_budget": report.compressed_tokens <= report.budget_tokens,
        })
    return {"fixture_messages": len(messages), "results": rows}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
