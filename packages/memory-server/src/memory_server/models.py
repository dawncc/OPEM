import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sessions: Mapped[list["Session"]] = relationship(back_populates="project")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("project_id", "external_session_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_session_id: Mapped[str] = mapped_column(String(200), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"))
    source_host: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="active")
    project: Mapped[Project] = relationship(back_populates="sessions")
    observations: Mapped[list["Observation"]] = relationship(back_populates="session")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session", order_by="ChatMessage.sequence")
    tool_executions: Mapped[list["ToolExecution"]] = relationship(back_populates="session", order_by="ToolExecution.created_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "sequence"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    session: Mapped[Session] = relationship(back_populates="chat_messages")
    tool_archive: Mapped["ToolExecution | None"] = relationship(back_populates="chat_message", uselist=False)


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    chat_message_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("chat_messages.id", ondelete="CASCADE"), unique=True)
    observation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("observations.id", ondelete="SET NULL"), nullable=True, unique=True)
    tool_name: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    input_text: Mapped[str | None] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)
    error_reason: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(50), index=True)
    evidence: Mapped[str | None] = mapped_column(Text)
    failure_signature: Mapped[str | None] = mapped_column(String(64), index=True)
    faq_question: Mapped[str | None] = mapped_column(Text)
    faq_answer: Mapped[str | None] = mapped_column(Text)
    derivation_version: Mapped[str] = mapped_column(String(20), default="v1")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    project: Mapped[Project] = relationship()
    session: Mapped[Session] = relationship(back_populates="tool_executions")
    chat_message: Mapped[ChatMessage] = relationship(back_populates="tool_archive")
    observation: Mapped["Observation | None"] = relationship()


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    content: Mapped[str] = mapped_column(Text)
    concepts: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON, "sqlite"), default=list)
    files: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON, "sqlite"), default=list)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB().with_variant(JSON, "sqlite"), default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session: Mapped[Session | None] = relationship(back_populates="observations")


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    memory_type: Mapped[str] = mapped_column(String(40), index=True)
    concepts: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON, "sqlite"), default=list)
    files: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON, "sqlite"), default=list)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384).with_variant(JSON, "sqlite"), nullable=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, index=True)
    sources: Mapped[list["MemorySource"]] = relationship(back_populates="memory", cascade="all, delete-orphan")


class MemorySource(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (UniqueConstraint("memory_id", "observation_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("memories.id", ondelete="CASCADE"))
    observation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("observations.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(String(40), default="derived_from")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    memory: Mapped[Memory] = relationship(back_populates="sources")
    observation: Mapped[Observation] = relationship()


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    observation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("observations.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(50), default="compress_observation")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("project_id", "summary_date"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    summary_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    memory_count: Mapped[int] = mapped_column(Integer, default=0)
    decisions: Mapped[list[str]] = mapped_column(JSON, default=list)
    learnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    unresolved: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    project: Mapped[Project] = relationship()
