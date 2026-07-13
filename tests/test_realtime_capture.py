import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from memory_common.schemas import ChatBatchCreate
from memory_server.db import Base
from memory_server.models import ChatMessage, Observation, ProcessingJob, Session
from memory_server.services import create_chat_batch
from memory_common.codex_history import parse_rollout


def load_capture_module():
    path = Path(__file__).parents[1] / "hooks" / "capture.py"
    spec = importlib.util.spec_from_file_location("codex_memory_capture", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_server_allocates_sequences_and_deduplicates_hook_events():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(engine, expire_on_commit=False)
    with LocalSession() as db:
        first = ChatBatchCreate(
            project="demo", session_id="thread-1", session_status="active",
            messages=[{"role": "user", "content": "implement capture", "event_id": "turn:1:user"}],
        )
        session, accepted, duplicates = create_chat_batch(db, first)
        assert (accepted, duplicates) == (1, 0)
        assert session.status == "active"

        completed = ChatBatchCreate(
            project="demo", session_id="thread-1", session_status="completed",
            session_summary="done",
            messages=[{"role": "assistant", "content": "done", "event_id": "turn:1:assistant"}],
        )
        session, accepted, duplicates = create_chat_batch(db, completed)
        assert (accepted, duplicates) == (1, 0)
        assert session.status == "completed"
        assert session.summary == "done"
        assert [item.sequence for item in db.scalars(select(ChatMessage).order_by(ChatMessage.sequence))] == [0, 1]

        _, accepted, duplicates = create_chat_batch(db, completed)
        assert (accepted, duplicates) == (0, 1)
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 2


def test_hook_maps_request_tool_and_stop_to_immediate_deliveries(tmp_path, monkeypatch):
    capture = load_capture_module()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config_dir = tmp_path / "codex-memory"
    config_dir.mkdir()
    (config_dir / "client.json").write_text('{"server":"http://memory.test"}', encoding="utf-8")
    deliveries = []
    monkeypatch.setattr(capture, "deliver", lambda server, endpoint, payload: deliveries.append((server, endpoint, payload)))

    common = {"session_id": "session-1", "turn_id": "turn-1", "cwd": str(tmp_path / "project")}
    capture.handle({**common, "hook_event_name": "UserPromptSubmit", "prompt": "please fix it"})
    capture.handle({
        **common, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "tool-1",
        "tool_input": {"command": "pytest"}, "tool_response": {"exit_code": 1, "output": "failed"},
    })
    capture.handle({**common, "hook_event_name": "Stop", "last_assistant_message": "fixed"})

    assert [item[1] for item in deliveries] == [
        "/api/v1/chat/messages", "/api/v1/chat/messages",
        "/api/v1/chat/messages", "/api/v1/observations",
    ]
    assert deliveries[0][2]["messages"][0]["event_id"] == "turn:turn-1:user"
    assert deliveries[1][2]["messages"][0]["metadata"]["status"] == "failed"
    assert deliveries[2][2]["session_status"] == "completed"
    assert deliveries[3][2]["idempotency_key"]


def test_hook_delivery_is_persisted_before_network_and_retried(tmp_path, monkeypatch):
    capture = load_capture_module()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    attempts = []

    def offline(server, endpoint, payload):
        attempts.append((server, endpoint, payload))
        raise OSError("server unavailable")

    monkeypatch.setattr(capture, "request_json", offline)
    capture.deliver("http://memory.test", "/api/v1/chat/messages", {"event_id": "event-1"})
    queued = list((tmp_path / "codex-memory" / "pending.d").glob("*.json"))
    assert len(queued) == 1
    assert json.loads(queued[0].read_text(encoding="utf-8"))["event_id"] == "event-1"

    delivered = []
    monkeypatch.setattr(capture, "request_json", lambda server, endpoint, payload: delivered.append((endpoint, payload)) or {})
    assert capture.flush_pending("http://memory.test") == 1
    assert delivered == [("/api/v1/chat/messages", {"event_id": "event-1"})]
    assert not list((tmp_path / "codex-memory" / "pending.d").glob("*.json"))


def test_concurrent_hook_queue_writes_do_not_drop_events(tmp_path, monkeypatch):
    capture = load_capture_module()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: capture.append_pending(
            "/api/v1/chat/messages", {"event_id": f"event-{index}"},
        ), range(40)))

    queued = list((tmp_path / "codex-memory" / "pending.d").glob("*.json"))
    assert len(queued) == 40
    assert {json.loads(path.read_text(encoding="utf-8"))["event_id"] for path in queued} == {
        f"event-{index}" for index in range(40)
    }


def test_current_hook_event_is_delivered_before_offline_backlog(tmp_path, monkeypatch):
    capture = load_capture_module()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    capture.append_pending("/api/v1/chat/messages", {"event_id": "older"})
    delivered = []
    monkeypatch.setattr(capture, "request_json", lambda server, endpoint, payload: delivered.append(payload["event_id"]) or {})

    capture.deliver("http://memory.test", "/api/v1/chat/messages", {"event_id": "current"})

    assert delivered == ["current", "older"]
    assert not list((tmp_path / "codex-memory" / "pending.d").glob("*.json"))


def test_hook_main_records_ignored_payload_for_diagnosis(tmp_path, monkeypatch):
    capture = load_capture_module()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(capture.sys, "stdin", StringIO(json.dumps({
        "hook_event_name": "UnknownEvent", "session_id": "session-1", "turn_id": "turn-1",
    })))

    assert capture.main() == 0
    status = json.loads((tmp_path / "codex-memory" / "capture-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ignored"
    assert status["hook_event_name"] == "UnknownEvent"
    assert status["payload_keys"] == ["hook_event_name", "session_id", "turn_id"]


def test_turn_observation_is_processed_into_memory_input():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(engine, expire_on_commit=False)
    # This guards the server-side contract used by the Stop hook: each accepted
    # turn observation gets one asynchronous processing job.
    from memory_common.schemas import ObservationCreate
    from memory_server.services import create_observation
    with LocalSession() as db:
        observation, duplicate = create_observation(db, ObservationCreate(
            content="Implemented real-time capture", kind="observation", project="demo",
            session_id="session-1", idempotency_key="turn-observation-1",
            metadata={"capture": "codex-hook"},
        ))
        assert not duplicate
        assert db.scalar(select(func.count()).select_from(Observation)) == 1
        assert db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.observation_id == observation.id)) == 1


def test_history_import_and_live_hook_share_event_ids_without_duplicates(tmp_path):
    rollout = tmp_path / "rollout-2026-07-12T00-00-00-session-history.jsonl"
    records = [
        {"timestamp": "2026-07-12T00:00:00Z", "type": "session_meta", "payload": {"id": "session-history", "cwd": str(tmp_path / "demo")}},
        {"timestamp": "2026-07-12T00:00:01Z", "type": "turn_context", "payload": {"turn_id": "turn-1", "cwd": str(tmp_path / "demo")}},
        {"timestamp": "2026-07-12T00:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix tests"}], "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}},
        {"timestamp": "2026-07-12T00:00:03Z", "type": "response_item", "payload": {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "shell", "arguments": "pytest", "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}},
        {"timestamp": "2026-07-12T00:00:04Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": {"exit_code": 0, "output": "2 passed"}, "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}},
        {"timestamp": "2026-07-12T00:00:05Z", "type": "response_item", "payload": {"type": "message", "id": "msg-1", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": "fixed"}], "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"}}},
        {"timestamp": "2026-07-12T00:00:06Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "fixed", "duration_ms": 1200}},
    ]
    rollout.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    parsed = parse_rollout(rollout)
    assert parsed and parsed.completed_turns == 1 and not parsed.has_active_turn
    assert [item["event_id"] for item in parsed.messages] == [
        "turn:turn-1:user", "tool:call-1", "turn:turn-1:assistant",
    ]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(engine, expire_on_commit=False)
    with LocalSession() as db:
        historical = ChatBatchCreate(
            project=parsed.project, session_id=parsed.session_id, session_status="completed",
            deduplicate_by_content=True, messages=parsed.messages,
        )
        _, accepted, duplicates = create_chat_batch(db, historical)
        assert (accepted, duplicates) == (3, 0)
        live = ChatBatchCreate(project=parsed.project, session_id=parsed.session_id, messages=[
            {"role": "user", "content": "fix tests", "event_id": "turn:turn-1:user"},
            {"role": "tool", "content": json.dumps({"exit_code": 0, "output": "2 passed"}), "event_id": "tool:call-1"},
            {"role": "assistant", "content": "fixed", "event_id": "turn:turn-1:assistant"},
        ])
        _, accepted, duplicates = create_chat_batch(db, live)
        assert (accepted, duplicates) == (0, 3)
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 3


def test_history_content_fallback_deduplicates_legacy_rows_without_event_ids():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(engine, expire_on_commit=False)
    with LocalSession() as db:
        legacy = ChatBatchCreate(project="demo", session_id="legacy", messages=[
            {"role": "user", "content": "same request", "sequence": 0},
            {"role": "assistant", "content": "same answer", "sequence": 1},
        ])
        create_chat_batch(db, legacy)
        historical = ChatBatchCreate(project="demo", session_id="legacy", deduplicate_by_content=True, messages=[
            {"role": "user", "content": "same request", "event_id": "turn:old:user"},
            {"role": "assistant", "content": "same answer", "event_id": "turn:old:assistant"},
        ])
        _, accepted, duplicates = create_chat_batch(db, historical)
        assert (accepted, duplicates) == (0, 2)
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 2


def test_history_parser_skips_unfinished_turns(tmp_path):
    rollout = tmp_path / "rollout-2026-07-12T00-00-00-active.jsonl"
    records = [
        {"timestamp": "2026-07-12T00:00:00Z", "type": "session_meta", "payload": {"id": "active", "cwd": str(tmp_path)}},
        {"timestamp": "2026-07-12T00:00:01Z", "type": "turn_context", "payload": {"turn_id": "turn-active"}},
        {"timestamp": "2026-07-12T00:00:02Z", "type": "event_msg", "payload": {"type": "task_started"}},
        {"timestamp": "2026-07-12T00:00:03Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "still running"}], "internal_chat_message_metadata_passthrough": {"turn_id": "turn-active"}}},
    ]
    rollout.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    assert parse_rollout(rollout) is None


def test_history_sync_and_hook_can_submit_same_event_concurrently(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    LocalSession = sessionmaker(engine, expire_on_commit=False)
    payloads = [
        ChatBatchCreate(project="demo", session_id="concurrent", messages=[
            {"role": "assistant", "content": "done", "event_id": "turn:1:assistant"},
        ]),
        ChatBatchCreate(project="demo", session_id="concurrent", deduplicate_by_content=True, messages=[
            {"role": "assistant", "content": "done", "event_id": "turn:1:assistant"},
        ]),
    ]

    def submit(payload):
        with LocalSession() as db:
            _, accepted, duplicates = create_chat_batch(db, payload)
            return accepted, duplicates

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, payloads))
    assert sorted(results) == [(0, 1), (1, 0)]
    with LocalSession() as db:
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 1
