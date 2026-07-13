from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ObservationKind(StrEnum):
    observation = "observation"
    decision = "decision"
    learning = "learning"
    problem = "problem"
    solution = "solution"
    session_summary = "session_summary"


class ObservationCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    kind: ObservationKind
    project: str = Field(min_length=1, max_length=200)
    session_id: str | None = Field(default=None, max_length=200)
    concepts: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    importance: int = Field(default=3, ge=1, le=5)
    source_host: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubmitResponse(BaseModel):
    observation_id: UUID
    session_id: UUID | None
    status: str = "accepted"
    duplicate: bool = False


class ChatMessageCreate(BaseModel):
    role: str = Field(pattern="^(user|assistant|system|tool)$")
    content: str = Field(min_length=1, max_length=500_000)
    sequence: int | None = Field(default=None, ge=0)
    event_id: str | None = Field(default=None, min_length=1, max_length=255)
    created_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatBatchCreate(BaseModel):
    project: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    source_host: str | None = Field(default=None, max_length=255)
    session_status: str | None = Field(default=None, pattern="^(active|completed)$")
    session_summary: str | None = Field(default=None, max_length=100_000)
    deduplicate_by_content: bool = False
    messages: list[ChatMessageCreate] = Field(min_length=1, max_length=2000)


class ChatBatchResponse(BaseModel):
    session_id: UUID
    accepted: int
    duplicates: int


class RecallRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    project: str | None = None
    limit: int = Field(default=8, ge=1, le=50)
    session_id: str | None = Field(default=None, max_length=200)
    turn_id: str | None = Field(default=None, max_length=255)
    policy_version: str | None = Field(default=None, max_length=80)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class RecallItem(BaseModel):
    memory_id: UUID
    title: str
    content: str
    memory_type: str
    project: str
    concepts: list[str]
    files: list[str]
    importance: int
    confidence: float
    score: float
    matched_by: list[str] = Field(default_factory=list)
    score_details: dict[str, float] = Field(default_factory=dict)
    source_sessions: list[str]
    source_observations: list[UUID]
    updated_at: datetime


class RecallResponse(BaseModel):
    items: list[RecallItem]
    context: str = ""
    recall_event_id: UUID | None = None


class MemoryFeedbackCreate(BaseModel):
    memory_id: UUID
    outcome: str = Field(pattern="^(helpful|irrelevant|harmful)$")
    query: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=4000)
    session_id: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, max_length=128)


class MemoryFeedbackResponse(BaseModel):
    feedback_id: UUID
    memory_id: UUID
    duplicate: bool = False
    helpful: int
    irrelevant: int
    harmful: int
    confidence: float


class HealthResponse(BaseModel):
    status: str
    database: str
    pending_jobs: int


class OutcomeEvidenceCreate(BaseModel):
    project: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=255)
    evidence_type: str = Field(min_length=1, max_length=50)
    metric: str = Field(default="task_success", min_length=1, max_length=80)
    value: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    strength: str = Field(default="strong", pattern="^(strong|medium|weak)$")
    source_type: str = Field(default="agent_report", min_length=1, max_length=40)
    source_ref: str | None = Field(default=None, max_length=500)
    rationale: str | None = Field(default=None, max_length=4000)
    independence_group: str = Field(default="explicit_outcome", min_length=1, max_length=80)
    idempotency_key: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutcomeEvidenceResponse(BaseModel):
    task_run_id: UUID
    evidence_id: UUID
    duplicate: bool = False
    quality_score: float | None = None
    quality_confidence: float = 0.0
    quality_status: str = "unknown"


class RouteRecommendationRequest(BaseModel):
    project: str = Field(min_length=1, max_length=200)
    task: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=200)
    turn_id: str | None = Field(default=None, max_length=255)
    task_kind: str | None = Field(default=None, max_length=40)
    risk_level: str = Field(default="unknown", pattern="^(unknown|low|medium|high)$")
    available_model_levels: list[str] = Field(default_factory=list, max_length=20)
    current_model: str | None = Field(default=None, max_length=200)


class RouteRecommendationResponse(BaseModel):
    assignment_id: UUID
    mode: str
    route_arm: str
    policy_version: str
    model_level: str | None = None
    reasoning_effort: str | None = None
    max_agents: int = 0
    max_tool_calls: int = 0
    verification_mode: str = "targeted"
    assignment_probability: float = 1.0
    reasons: list[str] = Field(default_factory=list)


class PathInterventionCreate(BaseModel):
    project: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=255)
    path_key: str = Field(min_length=1, max_length=300)
    method: str = Field(pattern="^(paired_replay|randomized)$")
    full_quality: float = Field(ge=0.0, le=1.0)
    counterfactual_quality: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    idempotency_key: str | None = Field(default=None, max_length=128)
    rationale: str | None = Field(default=None, max_length=4000)


class PathInterventionResponse(BaseModel):
    path_score_id: UUID
    evidence_id: UUID
    necessity: float
    importance: float
    efficiency: float | None = None
    duplicate: bool = False
