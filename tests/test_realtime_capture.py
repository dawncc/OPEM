import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from memory_common.schemas import ChatBatchCreate
from memory_server.db import Base
from memory_server.models import ChatMessage, Observation, ProcessingJob, Session
from memory_server.services import create_chat_batch


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
