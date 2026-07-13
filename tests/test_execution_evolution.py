from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from memory_common.schemas import ChatBatchCreate, OutcomeEvidenceCreate, PathInterventionCreate, RouteRecommendationRequest
from memory_server.db import Base
from memory_server.models import EvaluationJob, PathScore, Project, StrategyVersion, TaskRun
from memory_server.outcomes import process_evaluation_job, record_outcome_evidence
from memory_server.path_scoring import record_path_intervention
from memory_server.services import create_chat_batch
from memory_server.strategy import evaluate_execution_strategies, propose_shadow_strategy, recommend_route


def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'evolution.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def captured_task(db, *, turn_id="turn-1", tool_input="pytest -q", tool_output="2 passed", model="gpt-test"):
    payload = ChatBatchCreate(
        project="evolution-project",
        session_id="session-1",
        session_status="completed",
        messages=[
            {
                "role": "user", "content": "fix the failing tests", "sequence": 0,
                "event_id": f"turn:{turn_id}:user",
                "metadata": {"turn_id": turn_id, "model": model, "reasoning_effort": "high"},
            },
            {
                "role": "tool", "content": tool_output, "sequence": 1,
                "event_id": f"tool:{turn_id}",
                "metadata": {
                    "turn_id": turn_id, "tool_name": "shell", "tool_use_id": f"call-{turn_id}",
                    "tool_input": tool_input, "status": "success", "duration_ms": 120,
                },
            },
            {
                "role": "assistant", "content": "Fixed and verified.", "sequence": 2,
                "event_id": f"turn:{turn_id}:assistant", "duration_ms": 900,
                "metadata": {
                    "turn_id": turn_id, "model": model, "reasoning_effort": "high",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                },
            },
        ],
    )
    create_chat_batch(db, payload)
    return db.scalar(select(TaskRun).where(TaskRun.turn_id == turn_id))


def test_chat_capture_derives_task_dag_and_queues_evaluation(tmp_path):
    SessionFactory = database(tmp_path)
    with SessionFactory() as db:
        task = captured_task(db)
        assert task.model_name == "gpt-test"
        assert task.reasoning_effort == "high"
        assert task.status == "completed"
        assert task.capture_completeness == 1.0
        assert [node.node_type for node in task.nodes] == ["request", "validator", "response"]
        assert db.scalar(select(EvaluationJob).where(EvaluationJob.task_run_id == task.id)) is not None


def test_rule_evidence_scores_verified_task_but_keeps_causal_fields_empty(tmp_path):
    SessionFactory = database(tmp_path)
    with SessionFactory() as db:
        task = captured_task(db)
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.task_run_id == task.id))
        process_evaluation_job(db, job)
        db.commit()
        db.refresh(task)
        assert task.quality_status == "success"
        assert task.quality_score == 1.0
        score = db.scalar(select(PathScore).where(PathScore.task_run_id == task.id))
        assert score.validation_value == 1.0
        assert score.necessity is None
        assert score.importance is None
        assert score.evidence_method == "observational"


def test_missing_validator_evidence_remains_unknown(tmp_path):
    SessionFactory = database(tmp_path)
    with SessionFactory() as db:
        task = captured_task(db, tool_input="rg TODO packages", tool_output="no matches")
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.task_run_id == task.id))
        process_evaluation_job(db, job)
        db.commit()
        db.refresh(task)
        assert task.quality_score is None
        assert task.quality_status == "unknown"


def test_explicit_evidence_enables_reporting_but_not_active_routing(tmp_path):
    SessionFactory = database(tmp_path)
    with SessionFactory() as db:
        task = captured_task(db, tool_input="rg TODO packages", tool_output="no matches")
        updated, evidence, duplicate = record_outcome_evidence(db, OutcomeEvidenceCreate(
            project="evolution-project", session_id="session-1", turn_id=task.turn_id,
            evidence_type="user_acceptance", value=1.0, confidence=1.0,
            strength="strong", source_type="user", idempotency_key="accept-1",
        ))
        assert not duplicate
        assert evidence.task_run_id == task.id
        assert updated.quality_status == "success"

        assignment, strategy, config, reasons = recommend_route(db, RouteRecommendationRequest(
            project="evolution-project", task="fix the tests", session_id="session-1",
            turn_id=task.turn_id, risk_level="low", current_model="medium",
            available_model_levels=["medium", "high"],
        ))
        assert assignment.mode == "observe"
        assert strategy.version == "baseline-v1"
        assert config["model_level"] == "medium"
        assert reasons
        assert db.scalar(select(StrategyVersion).where(StrategyVersion.project_id == task.project_id)) is not None

        report = evaluate_execution_strategies(db, "evolution-project")
        assert report[0]["strong_cases"] == 1
        assert not report[0]["promotion_allowed"]
        candidate, candidate_reasons = propose_shadow_strategy(db, "evolution-project")
        assert candidate is None
        assert "shadow minimum" in candidate_reasons[0]


def test_path_necessity_requires_intervention_evidence(tmp_path):
    SessionFactory = database(tmp_path)
    with SessionFactory() as db:
        task = captured_task(db)
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.task_run_id == task.id))
        process_evaluation_job(db, job)
        db.commit()
        score = db.scalar(select(PathScore).where(PathScore.task_run_id == task.id))
        assert score.necessity is None

        updated, evidence, duplicate = record_path_intervention(db, PathInterventionCreate(
            project="evolution-project", session_id="session-1", turn_id=task.turn_id,
            path_key=score.path_key, method="paired_replay",
            full_quality=1.0, counterfactual_quality=0.4, confidence=0.9,
            idempotency_key="ablation-1",
        ))
        assert not duplicate
        assert evidence.metric == "path_quality_delta"
        assert updated.necessity == 0.6
        assert updated.importance == 0.6
        assert updated.evidence_method == "paired_replay"


def test_shadow_candidate_is_versioned_and_never_auto_activated(tmp_path, monkeypatch):
    SessionFactory = database(tmp_path)
    with SessionFactory() as db:
        task = captured_task(db)
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.task_run_id == task.id))
        process_evaluation_job(db, job)
        db.commit()
        monkeypatch.setattr(
            "memory_server.strategy.get_settings",
            lambda: SimpleNamespace(memory_strategy_min_shadow_cases=1),
        )
        candidate, reasons = propose_shadow_strategy(db, "evolution-project")
        assert candidate is not None
        assert candidate.status == "shadow"
        assert candidate.automatic
        assert candidate.config["route_arm"] == "shadow-auto-v1"
        assert reasons == ["candidate created in shadow mode; no runtime behavior changed"]
