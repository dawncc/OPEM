from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import memory_worker.main as worker_module
from memory_common.schemas import ObservationCreate
from memory_server.db import Base
from memory_server.models import ProcessingJob
from memory_server.services import create_observation, recall


SKILL_PATH = Path(__file__).parents[1] / "skill" / "codex-memory" / "SKILL.md"


def test_skill_requires_recall_and_proactive_memory():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Recall" in skill
    assert "Before substantial work, call `memory_recall`" in skill
    assert "## Proactive memory" in skill
    assert "without waiting for the user to ask" in skill
    assert "Do not save transient progress, guesses, secrets" in skill
    assert "Use one stable project identifier" in skill


def test_proactive_memory_becomes_recallable(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'memory.db'}")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine)

    with Session(engine) as db:
        observation, duplicate = create_observation(
            db,
            ObservationCreate(
                project="codex-lan-memory",
                kind="decision",
                content=(
                    "Use the stable project identifier codex-lan-memory for every memory call. "
                    "This prevents recall misses caused by path and display-name aliases."
                ),
                concepts=["project identity", "recall"],
                files=["skill/codex-memory/SKILL.md"],
                importance=5,
            ),
        )
        assert duplicate is False
        job_id = db.query(ProcessingJob.id).filter_by(observation_id=observation.id).scalar()

    monkeypatch.setattr(worker_module, "SessionLocal", local_session)
    monkeypatch.setattr(worker_module.embedder, "encode", lambda _: None)
    worker_module.process(job_id)

    with Session(engine) as db:
        results = recall(db, "stable project identifier recall aliases", "codex-lan-memory", 5)
        assert results
        assert results[0].memory_type == "decision"
        assert results[0].importance == 5
        assert "codex-lan-memory" in results[0].content
        assert results[0].source_observations == [observation.id]
        assert recall(db, "stable project identifier", "another-project", 5) == []
