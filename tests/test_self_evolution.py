from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from memory_common.schemas import MemoryFeedbackCreate
from memory_server.db import Base
from memory_server.models import Memory, Project
from memory_server.services import create_memory_feedback, feedback_confidence
from memory_server.evolution import (
    EvaluationMetrics,
    evaluate_policy,
    feedback_cases,
    promotion_allowed,
)


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


def test_feedback_replay_builds_labels_and_measures_safe_recall(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'replay.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(engine, expire_on_commit=False)
    with SessionFactory() as db:
        project = Project(name="replay-project")
        db.add(project)
        db.flush()
        helpful = Memory(
            project_id=project.id, title="PostgreSQL migration fix",
            content="Run the verified PostgreSQL migration.", memory_type="solution",
            concepts=["postgresql", "migration"], files=[], importance=4,
            confidence=0.9, search_text="PostgreSQL migration fix verified",
        )
        harmful = Memory(
            project_id=project.id, title="Obsolete PostgreSQL migration",
            content="Use the obsolete migration command.", memory_type="solution",
            concepts=["postgresql", "migration"], files=[], importance=3,
            confidence=0.1, search_text="obsolete PostgreSQL migration command",
        )
        db.add_all([helpful, harmful])
        db.commit()
        for memory, outcome in ((helpful, "helpful"), (harmful, "harmful")):
            create_memory_feedback(db, MemoryFeedbackCreate(
                memory_id=memory.id, outcome=outcome,
                query="PostgreSQL migration fix", session_id=f"session-{outcome}",
            ))

        cases = feedback_cases(db, "replay-project")
        metrics = evaluate_policy(db, cases, k=1)

        assert len(cases) == 1
        assert cases[0].helpful_ids == {helpful.id}
        assert cases[0].harmful_ids == {harmful.id}
        assert metrics.recall_at_k == 1.0
        assert metrics.mrr == 1.0
        assert metrics.harmful_recall_rate == 0.0


def test_promotion_gate_requires_enough_cases_and_no_safety_regression():
    baseline = EvaluationMetrics(30, 30, 0.8, 0.7, 0.1, 0.2)
    improved = EvaluationMetrics(30, 30, 0.85, 0.75, 0.05, 0.2)
    unsafe = EvaluationMetrics(30, 30, 0.9, 0.8, 0.2, 0.1)
    too_small = EvaluationMetrics(5, 5, 1.0, 1.0, 0.0, 0.0)

    assert promotion_allowed(baseline, improved)
    assert not promotion_allowed(baseline, unsafe)
    assert not promotion_allowed(baseline, too_small)
