from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from memory_common.schemas import MemoryFeedbackCreate
from memory_server.db import Base
from memory_server.models import Memory, Project
from memory_server.services import create_memory_feedback, feedback_confidence


def test_feedback_confidence_rewards_helpful_and_penalizes_harmful():
    assert feedback_confidence({}) == 0.7
    assert feedback_confidence({"helpful": 3}) > 0.7
    assert feedback_confidence({"harmful": 1}) < feedback_confidence({"irrelevant": 1}) < 0.7


def test_feedback_is_auditable_idempotent_and_updates_ranking_confidence(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'feedback.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(engine, expire_on_commit=False)
    with SessionFactory() as db:
        project = Project(name="evolving-project")
        db.add(project)
        db.flush()
        memory = Memory(
            project_id=project.id, title="Known fix", content="Use the verified fix.",
            memory_type="solution", concepts=[], files=[], importance=3,
            confidence=0.7, search_text="Known fix verified",
        )
        db.add(memory)
        db.commit()
        memory_id = memory.id

        payload = MemoryFeedbackCreate(
            memory_id=memory_id, outcome="harmful", query="How do I fix it?",
            reason="The advice is obsolete", session_id="session-1", idempotency_key="feedback-1",
        )
        item, updated, counts, duplicate = create_memory_feedback(db, payload)
        assert not duplicate
        assert counts == {"harmful": 1}
        assert updated.confidence < 0.7
        assert item.reason == "The advice is obsolete"

        duplicate_item, _, duplicate_counts, duplicate = create_memory_feedback(db, payload)
        assert duplicate
        assert duplicate_item.id == item.id
        assert duplicate_counts == {"harmful": 1}
