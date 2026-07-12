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
    sequence: int = Field(ge=0)
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatBatchCreate(BaseModel):
    project: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    source_host: str | None = Field(default=None, max_length=255)
    messages: list[ChatMessageCreate] = Field(min_length=1, max_length=2000)


class ChatBatchResponse(BaseModel):
    session_id: UUID
    accepted: int
    duplicates: int


class RecallRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    project: str | None = None
    limit: int = Field(default=8, ge=1, le=50)

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


class HealthResponse(BaseModel):
    status: str
    database: str
    pending_jobs: int
