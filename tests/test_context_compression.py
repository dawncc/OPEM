from memory_common.context_compression import (
    CompressionPolicy,
    ContextMessage,
    compress_context,
    compress_tool_output,
    estimate_tokens,
)


def long(value: str, repeat: int = 80) -> str:
    return (value + " ") * repeat


def test_passthrough_below_budget():
    messages = [ContextMessage("user", "hello", sequence=0)]
    result, report = compress_context(messages, "hello", CompressionPolicy(max_input_tokens=1000, reserved_output_tokens=100))
    assert result == messages
    assert report.strategy == "passthrough"


def test_preserves_system_current_query_and_relevant_old_decision():
    messages = [
        ContextMessage("system", "Never expose secrets.", sequence=0),
        ContextMessage("user", long("讨论数据库"), sequence=1),
        ContextMessage("assistant", "决定数据库端口固定为 55432，这是必须约束。", sequence=2),
        ContextMessage("tool", long("ordinary build log"), sequence=3),
        ContextMessage("user", long("unrelated UI discussion"), sequence=4),
        ContextMessage("assistant", long("unrelated response"), sequence=5),
        ContextMessage("user", "数据库端口是什么？", sequence=6),
    ]
    result, report = compress_context(
        messages,
        "数据库端口是什么？",
        CompressionPolicy(max_input_tokens=850, reserved_output_tokens=150, recent_turns=1, summary_tokens=180, tool_output_tokens=80),
    )
    text = "\n".join(item.content for item in result)
    assert "Never expose secrets" in text
    assert "数据库端口是什么" in text
    assert "55432" in text
    assert report.compressed_tokens <= report.budget_tokens
    assert report.reduction_ratio > 0


def test_tool_compression_keeps_error_and_tail():
    content = "\n".join(["start"] + [f"line {i}" for i in range(100)] + ["ERROR timeout at db", "final hint"])
    compressed = compress_tool_output(content, 80)
    assert "ERROR timeout at db" in compressed
    assert "final hint" in compressed
    assert estimate_tokens(compressed) <= 85


def test_protected_message_survives_pressure():
    messages = [
        ContextMessage("system", long("policy"), {"protected": True}, 0),
        ContextMessage("user", long("old"), sequence=1),
        ContextMessage("assistant", long("reply"), sequence=2),
        ContextMessage("user", "current", sequence=3),
    ]
    result, _ = compress_context(messages, "current", CompressionPolicy(max_input_tokens=700, reserved_output_tokens=100, recent_turns=1))
    assert any(item.sequence == 0 for item in result)
    assert any(item.sequence == 3 for item in result)
