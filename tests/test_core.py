import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from memory_common.schemas import ChatBatchCreate, ChatMessageCreate, ObservationCreate, ObservationKind, RecallRequest
import memory_worker.main as worker_module
import memory_server.tool_grouping as tool_grouping_module
from memory_worker.main import build_daily_content, memory_type_for, rule_compress, similarity
from datetime import date
from types import SimpleNamespace
from memory_server.conversation import build_chat_turns, classify_tool_status, session_display_title, split_message_content, tool_failed
from memory_server.daily_archive import render_daily_markdown
from memory_server.db import Base
from memory_server.main import templates
from memory_server.models import Memory, Observation, ProcessingJob, ToolExecution
from memory_server.tool_archive import backfill_tool_profiles, error_category, extract_failure_reason, failure_signature, stringify_input
from memory_server.tool_grouping import TOOL_PROFILE_CACHE_KEY, execution_input, filter_tool_profiles, group_tool_executions, metadata_with_tool_profile, normalize_tool_input, profile_tool_execution, summarize_tool_groups, tool_group_facets, tool_operation
from memory_server.tracing import build_session_trace, classify_tool_kind, explicit_duration_ms, format_duration
from memory_server.costing import estimate_request_cost, format_usd
from memory_common.pending import append_pending, drain_pending, pending_path
from memory_common.schemas import RecallItem
from memory_server.services import bm25_scores, create_chat_batch, format_recall_context, reciprocal_rank_fusion, tokenize
from datetime import datetime, timezone


def test_observation_defaults_and_validation():
    item = ObservationCreate(content="Use pgvector for memory search", kind=ObservationKind.decision, project="demo")
    assert item.importance == 3
    assert item.concepts == []
    with pytest.raises(ValidationError):
        ObservationCreate(content="", kind=ObservationKind.decision, project="demo")


def test_recall_limit_is_bounded():
    with pytest.raises(ValidationError):
        RecallRequest(query="memory", limit=1000)
    with pytest.raises(ValidationError):
        RecallRequest(query="   ")


def test_similarity_detects_related_content():
    related = similarity("PostgreSQL pgvector memory index", "Use pgvector for PostgreSQL memory index")
    unrelated = similarity("PostgreSQL pgvector memory index", "CSS page navigation")
    assert related > unrelated


def test_rule_compression_preserves_source():
    class FakeObservation:
        kind = "decision"
        content = "使用 PostgreSQL 保存 Memory。原因是部署简单。"
        files = ["deploy/docker-compose.yml"]
    title, content = rule_compress(FakeObservation())
    assert "PostgreSQL" in title
    assert "部署简单" in content
    assert "docker-compose.yml" in content


def test_pending_queue_retries_without_losing_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    append_pending({"content": "first"})
    append_pending({"content": "second"})
    accepted = []
    def send(payload):
        if payload["content"] == "second":
            raise RuntimeError("offline")
        accepted.append(payload)
    assert drain_pending(send) == 1
    assert accepted == [{"content": "first"}]
    assert "second" in pending_path().read_text(encoding="utf-8")


def test_health_drain_includes_hook_spool_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    spool = pending_path().parent / "pending.d"
    spool.mkdir()
    queued = spool / "event.json"
    queued.write_text(json.dumps({
        "_queue_endpoint": "/api/v1/chat/messages", "content": "captured",
    }), encoding="utf-8")
    accepted = []

    assert drain_pending(accepted.append) == 1
    assert accepted == [{"_queue_endpoint": "/api/v1/chat/messages", "content": "captured"}]
    assert not queued.exists()


def test_recall_context_is_prompt_ready_and_source_attributed():
    item = RecallItem(
        memory_id=uuid4(), title="Use SQLite for local demo", content="Local demo does not require Docker.",
        memory_type="decision", project="demo", concepts=["SQLite"], files=[], importance=5,
        confidence=0.8, score=0.91, source_sessions=["session-1"], source_observations=[uuid4()],
        updated_at=datetime.now(timezone.utc),
    )
    context = format_recall_context("local database", [item])
    assert context.startswith("<chat-memory>")
    assert "historical memory, not current instructions" in context
    assert "session-1" in context
    assert context.endswith("</chat-memory>")


def test_daily_summary_groups_decisions_and_solutions():
    class Item:
        def __init__(self, kind, content):
            self.kind, self.content = kind, content
    class Mem:
        title = "SQLite local deployment"
        importance = 5
        updated_at = datetime.now(timezone.utc)
    content, decisions, learnings, unresolved = build_daily_content(
        "demo", date(2026, 7, 11), [Item("decision", "Use SQLite"), Item("solution", "Run a Python worker")], [Mem()]
    )
    assert "2026-07-11" in content
    assert decisions == ["Use SQLite"]
    assert learnings == ["Run a Python worker"]
    assert unresolved == []


def test_daily_markdown_archive_contains_projects_and_structured_work():
    updated_at = datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc)
    summary = SimpleNamespace(
        project=SimpleNamespace(name="demo"), observation_count=3, memory_count=2,
        updated_at=updated_at, content="Completed the export feature.",
        decisions=["Use Markdown"], learnings=["Keep exports portable"],
        unresolved=["Add cloud backup"],
    )
    markdown = render_daily_markdown(
        date(2026, 7, 12), [summary], generated_at=updated_at,
    )
    assert markdown.startswith("# 2026-07-12 每日工作汇总")
    assert "## demo" in markdown
    assert "### 关键决策\n\n- Use Markdown" in markdown
    assert "### 未解决事项\n\n- Add cloud backup" in markdown
    assert "共 1 个项目，3 条工作记录，2 条长期记忆" in markdown


def test_session_display_title_prefers_summary_then_user_message():
    session = SimpleNamespace(
        summary="  Finished the Markdown   archive. ", external_session_id="session-123",
        chat_messages=[SimpleNamespace(role="user", content="Please build an archive")],
    )
    assert session_display_title(session) == "Finished the Markdown archive."
    session.summary = None
    assert session_display_title(session) == "Please build an archive"
    session.chat_messages = [SimpleNamespace(role="assistant", content="Done")]
    assert session_display_title(session) == "session-123"


def test_chat_batch_preserves_roles_and_full_content():
    batch = ChatBatchCreate(project="demo", session_id="session-1", messages=[
        {"role": "user", "content": "完整的用户问题", "sequence": 0},
        {"role": "assistant", "content": "完整的助手回答\n包含第二行", "sequence": 1},
    ])
    assert [message.role for message in batch.messages] == ["user", "assistant"]
    assert "第二行" in batch.messages[1].content


def test_chat_message_duration_is_validated():
    message = ChatMessageCreate(role="tool", content="ok", sequence=0, duration_ms=245.5)
    assert message.duration_ms == 245.5
    with pytest.raises(ValidationError):
        ChatMessageCreate(role="tool", content="bad", sequence=0, duration_ms=-1)


def test_tokenize_supports_chinese_bigrams_and_english_terms():
    tokens = tokenize("SQLite 本机部署方案")
    assert "sqlite" in tokens
    assert "本机" in tokens
    assert "部署" in tokens


def test_bm25_ranks_relevant_document_first():
    query = tokenize("SQLite 本机部署")
    documents = [tokenize("使用 SQLite 进行本机部署"), tokenize("远程服务器 SSH 隧道"), tokenize("页面 CSS 优化")]
    scores = bm25_scores(query, documents)
    assert scores[0] == 1.0
    assert scores[0] > scores[1]


def test_rrf_rewards_consensus_across_retrievers():
    fused, matched = reciprocal_rank_fusion(
        {"BM25": [1.0, 0.5], "模糊匹配": [0.9, 0.0], "元数据": [0.8, 0.0]},
        {"BM25": 1.5, "模糊匹配": 0.7, "元数据": 0.9},
    )
    assert fused[0] > fused[1]
    assert matched[0] == ["BM25", "模糊匹配", "元数据"]


def chat_message(sequence, role, content, metadata=None):
    return SimpleNamespace(
        id=uuid4(), sequence=sequence, role=role, content=content,
        metadata_=metadata or {}, created_at=datetime.now(timezone.utc),
    )


def test_code_fences_are_split_without_rendering_raw_html():
    segments = split_message_content("说明\n```python\nprint('<script>')\n```\n完成")
    assert [segment["kind"] for segment in segments] == ["text", "code", "text"]
    assert "<script>" in segments[1]["content"]
    assert segments[1]["language"] == "python"


def test_chat_turns_group_user_led_rounds_and_classify_tools_code_errors():
    messages = [
        chat_message(0, "system", "project context"),
        chat_message(1, "user", "实现代码"),
        chat_message(2, "assistant", "```python\nprint('ok')\n```"),
        chat_message(3, "tool", "pytest failed: AssertionError", {"tool_name": "pytest", "status": "failed"}),
        chat_message(4, "user", "修复测试"),
        chat_message(5, "assistant", "已修复"),
    ]
    turns = build_chat_turns(messages)
    assert len(turns) == 3
    assert turns[0]["is_context"] is True
    assert {tag["label"] for tag in turns[1]["tags"]} == {"异常", "工具", "代码"}
    assert turns[1]["messages"][2]["tool_name"] == "pytest"
    assert turns[2]["title"] == "修复测试"


def test_tool_failure_uses_structured_status_before_output_text():
    assert tool_failed({"status": "completed"}, "Old Traceback from a previous retry") is False
    assert tool_failed({"status": "completed", "exit_code": 1}, "output") is True
    assert tool_failed({}, "0 errors, all tests passed") is False
    assert tool_failed({"is_error": True}, "output") is True
    assert classify_tool_status({"status": "failed", "exit_code": 0}, "output") == "failed"
    assert classify_tool_status({"status": "completed", "exit_code": 1}, "output") == "failed"
    assert classify_tool_status({}, "ordinary streaming output") == "unknown"


def test_failure_reason_and_signature_are_evidence_based_and_stable():
    output = "collecting tests\nFAILED test_api.py::test_submit\nAssertionError: expected 12 records"
    reason = extract_failure_reason(output, {})
    assert reason == "AssertionError: expected 12 records"
    assert error_category(reason) == "assertion"
    assert failure_signature("pytest", "assertion", reason) == failure_signature(
        "pytest", "assertion", "AssertionError: expected 99 records"
    )
    assert error_category("TypeError: fake_get() got an unexpected keyword argument 'timeout'") == "type_error"
    assert error_category("Authentication failed") == "authentication"
    assert error_category("Connection failed: refused") == "network"
    assert error_category("Command failed with exit code 2") == "process_exit"


def test_chat_tools_are_archived_and_only_known_outcomes_create_memories():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(engine, expire_on_commit=False)
    with LocalSession() as db:
        payload = ChatBatchCreate(project="archive-demo", session_id="tools-1", messages=[
            {"role": "tool", "content": "3 passed in 0.42s", "sequence": 0, "duration_ms": 420, "metadata": {"tool_name": "pytest", "status": "completed", "command": "pytest -q"}},
            {"role": "tool", "content": "FAILED test_api.py\nAssertionError: expected 2", "sequence": 1, "metadata": {"tool_name": "pytest", "exit_code": 1, "command": "pytest tests/test_api.py"}},
            {"role": "tool", "content": "Process started and is still streaming", "sequence": 2, "metadata": {"tool_name": "shell"}},
        ])
        _, accepted, duplicates = create_chat_batch(db, payload)
        assert (accepted, duplicates) == (3, 0)

        archives = {item.status: item for item in db.scalars(select(ToolExecution)).all()}
        assert set(archives) == {"success", "failed", "unknown"}
        assert archives["success"].output_text == "3 passed in 0.42s"
        assert archives["success"].metadata_["duration_ms"] == 420
        assert archives["success"].metadata_[TOOL_PROFILE_CACHE_KEY]["family"] == "testing"
        assert archives["success"].observation.kind == "learning"
        assert "tool-success" in archives["success"].observation.concepts
        assert archives["failed"].observation.kind == "problem"
        assert archives["failed"].faq_question == "为什么 pytest 工具执行失败？"
        assert "AssertionError" in archives["failed"].faq_answer
        assert archives["failed"].failure_signature
        assert archives["unknown"].observation_id is None
        assert db.scalar(select(func.count()).select_from(Observation)) == 2

        uncached_metadata = dict(archives["success"].metadata_)
        uncached_metadata.pop(TOOL_PROFILE_CACHE_KEY)
        archives["success"].metadata_ = uncached_metadata
        db.flush()
        assert backfill_tool_profiles(db) == 1
        assert archives["success"].metadata_[TOOL_PROFILE_CACHE_KEY]["version"] == "v2"
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 2

        _, accepted, duplicates = create_chat_batch(db, payload)
        assert (accepted, duplicates) == (0, 3)
        assert db.scalar(select(func.count()).select_from(ToolExecution)) == 3
        assert db.scalar(select(func.count()).select_from(Observation)) == 2


def test_functionally_similar_tools_are_grouped_with_usage_profiles():
    now = datetime.now(timezone.utc)
    project = SimpleNamespace(name="demo")
    calls = [
        SimpleNamespace(tool_name="shell", input_text="pytest -q", status="success", project=project, created_at=now),
        SimpleNamespace(tool_name="shell", input_text="pytest tests/test_api.py -q", status="failed", project=project, created_at=now.replace(microsecond=1)),
        SimpleNamespace(tool_name="shell", input_text="rg TODO packages", status="success", project=project, created_at=now.replace(microsecond=2)),
        SimpleNamespace(tool_name="web", input_text='{"search_query":[{"q":"memory server"}],"timeout_ms":1000}', status="unknown", project=project, created_at=now.replace(microsecond=3)),
        SimpleNamespace(tool_name="web", input_text='{"search_query":[{"q":"tool archive"}],"timeout_ms":3000}', status="success", project=project, created_at=now.replace(microsecond=4)),
    ]
    groups = group_tool_executions(calls)
    by_family = {group["family"]: group for group in groups}
    assert set(by_family) == {"search", "testing"}
    assert by_family["testing"]["count"] == 2
    assert by_family["testing"]["success_count"] == 1
    assert by_family["testing"]["failed_count"] == 1
    assert by_family["testing"]["languages"] == ["Python"]
    assert by_family["search"]["count"] == 3
    assert by_family["search"]["tool_names"] == ["shell", "web"]
    assert by_family["search"]["similarity"] >= 60
    assert groups[0]["count"] == 3
    summary = summarize_tool_groups(groups)
    assert summary == {
        "groups": 2, "calls": 5, "success": 3, "failed": 1, "unknown": 1,
        "success_rate": 60.0, "repeated_groups": 2, "projects": 1,
    }

    profiles = [profile_tool_execution(call) for call in calls]
    facets = tool_group_facets(profiles)
    assert {item["value"] for item in facets["families"]} == {"search", "testing"}
    assert [item["tool_name"] for item in filter_tool_profiles(profiles, family="testing")] == ["shell", "shell"]


def test_tool_input_normalization_removes_volatile_values_but_keeps_structure():
    first = normalize_tool_input('{"command":"pytest tests/test_api.py -q","limit":20,"request_id":"123e4567-e89b-12d3-a456-426614174000"}')
    second = normalize_tool_input('{"command":"pytest tests/test_api.py -q","limit":50,"request_id":"223e4567-e89b-12d3-a456-426614174999"}')
    assert first == second
    assert "pytest tests/test_api.py" in first
    assert tool_operation('{"command":"pytest tests/test_api.py"}') == "pytest"
    assert tool_operation('{"open":[{"ref_id":"result-1"}]}') == "open"
    assert tool_operation('const r = await tools.shell_command({command:"pytest -q"})') == "shell_command:pytest"
    legacy = SimpleNamespace(input_text=None, metadata_={"tool_input": {"open": [{"ref_id": "result-1"}]}})
    assert execution_input(legacy) == '{"open": [{"ref_id": "result-1"}]}'
    assert stringify_input(legacy.metadata_) == '{"open": [{"ref_id": "result-1"}]}'
    cached = SimpleNamespace(
        tool_name="shell", input_text="rg changed", output_text="different",
        metadata_=metadata_with_tool_profile({}, "shell", "pytest -q", "3 passed"),
    )
    assert profile_tool_execution(cached)["family"] == "testing"


def test_cached_tool_profile_avoids_recomputing_large_outputs(monkeypatch):
    metadata = metadata_with_tool_profile({}, "shell", "pytest -q", "3 passed")
    execution = SimpleNamespace(
        tool_name="shell", input_text="pytest -q", output_text="large output",
        metadata_=metadata,
    )
    monkeypatch.setattr(
        tool_grouping_module, "build_tool_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("profile recomputed")),
    )
    assert profile_tool_execution(execution)["family"] == "testing"


def test_worker_keeps_similar_tool_success_and_failure_memories_separate(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(engine, expire_on_commit=False)
    common = " ".join(f"shared_token_{index}" for index in range(80))
    with LocalSession() as db:
        payload = ChatBatchCreate(project="worker-separation", session_id="tools-2", messages=[
            {"role": "tool", "content": f"{common}\nBuild completed successfully", "sequence": 0, "metadata": {"tool_name": "build", "status": "completed", "exit_code": 0}},
            {"role": "tool", "content": f"{common}\nCommand failed with exit code 2", "sequence": 1, "metadata": {"tool_name": "build", "status": "failed", "exit_code": 2}},
        ])
        create_chat_batch(db, payload)
        jobs = db.scalars(select(ProcessingJob).order_by(ProcessingJob.created_at)).all()
        observations = [db.get(Observation, job.observation_id) for job in jobs]
        assert {memory_type_for(observation) for observation in observations} == {"tool_success", "tool_failure_faq"}
        search_texts = []
        for observation in observations:
            title, content = rule_compress(observation)
            search_texts.append(" ".join([title, content, *(observation.concepts or [])]))
        assert similarity(*search_texts) >= 0.58
        job_ids = [job.id for job in jobs]

    monkeypatch.setattr(worker_module, "SessionLocal", LocalSession)
    monkeypatch.setattr(worker_module.embedder, "encode", lambda _: None)
    for job_id in job_ids:
        worker_module.process(job_id)

    with LocalSession() as db:
        memories = db.scalars(select(Memory)).all()
        assert {memory.memory_type for memory in memories} == {"tool_success", "tool_failure_faq"}
        assert len(memories) == 2
        success = next(memory for memory in memories if memory.memory_type == "tool_success")
        failure = next(memory for memory in memories if memory.memory_type == "tool_failure_faq")
        assert "tool-success" in success.concepts and "FAQ" not in success.concepts
        assert "FAQ" in failure.concepts and "tool-success" not in failure.concepts


def test_session_trace_groups_requests_and_styles_tool_types():
    base = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    messages = [
        SimpleNamespace(id=uuid4(), sequence=0, role="user", content="运行测试并调用接口", metadata_={}, created_at=base),
        SimpleNamespace(id=uuid4(), sequence=1, role="assistant", content="开始执行", metadata_={}, created_at=base.replace(microsecond=400_000)),
        SimpleNamespace(id=uuid4(), sequence=2, role="tool", content="4 passed", metadata_={"tool_name": "pytest", "status": "completed", "duration_ms": 240, "command": "pytest -q"}, created_at=base.replace(microsecond=400_000)),
        SimpleNamespace(id=uuid4(), sequence=3, role="tool", content="Connection failed: refused", metadata_={"tool_name": "shell", "status": "failed", "duration_ms": 300, "command": "curl http://service"}, created_at=base.replace(microsecond=400_000)),
    ]
    session = SimpleNamespace(chat_messages=messages)
    trace = build_session_trace(session)
    assert trace["request_count"] == 1
    assert trace["tool_count"] == 2
    assert trace["failed_count"] == 1
    assert trace["active_duration_ms"] == 540
    assert [event["tool_kind"] for event in trace["paths"][0]["events"] if event["role"] == "tool"] == ["test", "network"]
    assert trace["paths"][0]["events"][1]["duration_accuracy"] == "estimated"
    assert trace["paths"][0]["events"][2]["duration_accuracy"] == "exact"


def test_trace_duration_parsing_and_tool_classification():
    assert explicit_duration_ms({"elapsed_seconds": 1.25}) == 1250
    assert explicit_duration_ms({"started_at": "2026-07-12T10:00:00+00:00", "ended_at": "2026-07-12T10:00:02+00:00"}) == 2000
    assert format_duration(240) == "240 ms"
    assert format_duration(1250) == "1.25 s"
    assert classify_tool_kind({"command": "apply_patch change.diff"}, "shell") == "file"
    assert classify_tool_kind({"command": "rg TODO packages"}, "shell") == "search"


def test_request_cost_prefers_usage_and_prices_cached_tokens():
    messages = [SimpleNamespace(role="assistant", content="done", metadata_={
        "model": "gpt-5.4", "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 100}
    })]
    cost = estimate_request_cost(messages)
    assert cost["accuracy"] == "usage"
    assert cost["input_tokens"] == 1000
    assert cost["cost_usd"] == pytest.approx(0.0022)
    assert format_usd(cost["cost_usd"]) == "$0.0022"


def test_request_cost_estimates_each_assistant_tool_round_trip():
    messages = [
        SimpleNamespace(role="user", content="hello", metadata_={}),
        SimpleNamespace(role="assistant", content="checking", metadata_={}),
        SimpleNamespace(role="tool", content="result " * 100, metadata_={}),
        SimpleNamespace(role="assistant", content="finished", metadata_={}),
    ]
    cost = estimate_request_cost(messages)
    assert cost["accuracy"] == "estimated"
    assert cost["input_tokens"] > cost["output_tokens"]
    assert cost["model_assumed"] is True


def test_request_cost_matches_variant_snapshot_before_base_model():
    messages = [SimpleNamespace(role="assistant", content="done", metadata_={
        "model": "gpt-5.4-mini-2026-03-17", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}
    })]
    cost = estimate_request_cost(messages)
    assert cost["price_model"] == "gpt-5.4-mini"
    assert cost["cost_usd"] == pytest.approx(0.75)


def test_trace_template_escapes_request_content():
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id=uuid4(), external_session_id="trace-safe", source_host="test", started_at=now,
        project=SimpleNamespace(id=uuid4(), name="demo"),
        chat_messages=[SimpleNamespace(id=uuid4(), sequence=0, role="user", content="<script>alert(1)</script>", metadata_={}, created_at=now)],
    )
    html = templates.env.get_template("trace.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path=f"/traces/{session.id}")), trace=build_session_trace(session)
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_session_template_escapes_chat_html():
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        external_session_id="safe-session", status="active", source_host="test", started_at=now,
        summary=None, project=SimpleNamespace(id=uuid4(), name="demo"), chat_messages=[1], observations=[],
    )
    message = {
        "role": "assistant", "role_label": "Codex", "sequence": 1, "created_at": now,
        "segments": [{"kind": "text", "content": "<script>alert(1)</script>"}],
    }
    turn = {"number": 1, "anchor": "turn-1", "title": "safe", "messages": [message], "tags": [{"label": "对话", "tone": "chat"}], "is_context": False}
    html = templates.env.get_template("session.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/sessions/test")), session=session,
        memories=[], chat_turns=[turn], user_turn_count=1,
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
