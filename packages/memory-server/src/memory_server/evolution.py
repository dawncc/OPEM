"""Offline replay and promotion gates for feedback-driven recall evolution."""

from dataclasses import asdict, dataclass
from itertools import product

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .models import Memory, MemoryFeedback, Project
from .services import DEFAULT_RECALL_POLICY, RecallPolicy, recall


@dataclass(frozen=True)
class EvaluationCase:
    query: str
    project: str
    helpful_ids: frozenset
    harmful_ids: frozenset
    irrelevant_ids: frozenset


@dataclass(frozen=True)
class EvaluationMetrics:
    cases: int
    labeled_cases: int
    recall_at_k: float
    mrr: float
    harmful_recall_rate: float
    irrelevant_recall_rate: float


@dataclass(frozen=True)
class PolicyEvaluation:
    policy: RecallPolicy
    metrics: EvaluationMetrics


def feedback_cases(db: DbSession, project: str | None = None) -> list[EvaluationCase]:
    """Build deterministic query-level labels from auditable explicit feedback."""
    stmt = (
        select(MemoryFeedback, Project.name)
        .join(Memory, MemoryFeedback.memory_id == Memory.id)
        .join(Project, Memory.project_id == Project.id)
        .where(MemoryFeedback.query.is_not(None), MemoryFeedback.query != "")
        .order_by(MemoryFeedback.created_at, MemoryFeedback.id)
    )
    if project:
        stmt = stmt.where(Project.name == project)
    grouped: dict[tuple[str, str], dict[str, set]] = {}
    for feedback, project_name in db.execute(stmt):
        labels = grouped.setdefault(
            (project_name, feedback.query.strip()),
            {"helpful": set(), "harmful": set(), "irrelevant": set()},
        )
        labels[feedback.outcome].add(feedback.memory_id)
    return [
        EvaluationCase(query, project_name, frozenset(labels["helpful"]),
                       frozenset(labels["harmful"]), frozenset(labels["irrelevant"]))
        for (project_name, query), labels in grouped.items()
    ]


def evaluate_policy(db: DbSession, cases: list[EvaluationCase], policy: RecallPolicy = DEFAULT_RECALL_POLICY, k: int = 5) -> EvaluationMetrics:
    reciprocal_ranks: list[float] = []
    helpful_hits = harmful_hits = irrelevant_hits = 0
    helpful_cases = harmful_cases = irrelevant_cases = 0
    for case in cases:
        ranked_ids = [item.memory_id for item in recall(db, case.query, case.project, k, policy)]
        if case.helpful_ids:
            helpful_cases += 1
            ranks = [index + 1 for index, memory_id in enumerate(ranked_ids) if memory_id in case.helpful_ids]
            helpful_hits += bool(ranks)
            reciprocal_ranks.append(1 / min(ranks) if ranks else 0.0)
        if case.harmful_ids:
            harmful_cases += 1
            harmful_hits += any(memory_id in case.harmful_ids for memory_id in ranked_ids)
        if case.irrelevant_ids:
            irrelevant_cases += 1
            irrelevant_hits += any(memory_id in case.irrelevant_ids for memory_id in ranked_ids)
    return EvaluationMetrics(
        cases=len(cases), labeled_cases=helpful_cases,
        recall_at_k=round(helpful_hits / helpful_cases, 4) if helpful_cases else 0.0,
        mrr=round(sum(reciprocal_ranks) / helpful_cases, 4) if helpful_cases else 0.0,
        harmful_recall_rate=round(harmful_hits / harmful_cases, 4) if harmful_cases else 0.0,
        irrelevant_recall_rate=round(irrelevant_hits / irrelevant_cases, 4) if irrelevant_cases else 0.0,
    )


def promotion_allowed(baseline: EvaluationMetrics, candidate: EvaluationMetrics, min_cases: int = 20, max_recall_regression: float = 0.0, max_harmful_increase: float = 0.0) -> bool:
    """Conservative gate: effectiveness cannot regress and safety cannot worsen."""
    return (
        candidate.labeled_cases >= min_cases
        and candidate.recall_at_k >= baseline.recall_at_k - max_recall_regression
        and candidate.mrr >= baseline.mrr - max_recall_regression
        and candidate.harmful_recall_rate <= baseline.harmful_recall_rate + max_harmful_increase
    )


def search_policies(db: DbSession, cases: list[EvaluationCase], k: int = 5) -> list[PolicyEvaluation]:
    """Small, auditable grid search; callers still apply the promotion gate."""
    candidates = (
        RecallPolicy(fusion, confidence, diversity)
        for fusion, confidence, diversity in product((0.55, 0.65, 0.75), (0.05, 0.10, 0.20), (0.10, 0.18, 0.25))
    )
    results = [PolicyEvaluation(policy, evaluate_policy(db, cases, policy, k)) for policy in candidates]
    return sorted(results, key=lambda item: (item.metrics.harmful_recall_rate, -item.metrics.recall_at_k, -item.metrics.mrr, tuple(asdict(item.policy).values())))
