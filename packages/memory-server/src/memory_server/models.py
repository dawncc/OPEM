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
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="session", order_by="TaskRun.started_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence"),
        UniqueConstraint("session_id", "event_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
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
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    capability: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_status: Mapped[str] = mapped_column(String(20), default="unknown")
    concepts: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON, "sqlite"), default=list)
    files: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON, "sqlite"), default=list)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384).with_variant(JSON, "sqlite"), nullable=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, index=True)
    sources: Mapped[list["MemorySource"]] = relationship(back_populates="memory", cascade="all, delete-orphan")
    feedback: Mapped[list["MemoryFeedback"]] = relationship(back_populates="memory", cascade="all, delete-orphan")


class MemoryFeedback(Base):
    __tablename__ = "memory_feedback"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("memories.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(20), index=True)
    query: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(200), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    memory: Mapped[Memory] = relationship(back_populates="feedback")


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


class TaskRun(Base):
    """One user-led request and its observable execution path."""

    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_id"),
        Index("ix_task_runs_project_started", "project_id", "started_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str] = mapped_column(String(255), index=True)
    request_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    response_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    task_kind: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(40), nullable=True)
    route_arm: Mapped[str] = mapped_column(String(80), default="observed", index=True)
    policy_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    assignment_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capture_completeness: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    severe_failure: Mapped[bool] = mapped_column(default=False, index=True)
    derivation_version: Mapped[str] = mapped_column(String(30), default="task-v1")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    project: Mapped[Project] = relationship()
    session: Mapped[Session] = relationship(back_populates="task_runs")
    request_message: Mapped[ChatMessage | None] = relationship(foreign_keys=[request_message_id])
    response_message: Mapped[ChatMessage | None] = relationship(foreign_keys=[response_message_id])
    nodes: Mapped[list["ExecutionNode"]] = relationship(back_populates="task_run", cascade="all, delete-orphan", order_by="ExecutionNode.sequence")
    evidence: Mapped[list["OutcomeEvidence"]] = relationship(back_populates="task_run", cascade="all, delete-orphan")
    path_scores: Mapped[list["PathScore"]] = relationship(back_populates="task_run", cascade="all, delete-orphan")


class ExecutionNode(Base):
    __tablename__ = "execution_nodes"
    __table_args__ = (
        UniqueConstraint("task_run_id", "node_key"),
        Index("ix_execution_nodes_task_sequence", "task_run_id", "sequence"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("task_runs.id", ondelete="CASCADE"), index=True)
    parent_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("execution_nodes.id", ondelete="SET NULL"), nullable=True)
    chat_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, unique=True)
    tool_execution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tool_executions.id", ondelete="SET NULL"), nullable=True, unique=True)
    node_key: Mapped[str] = mapped_column(String(300))
    branch_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    node_type: Mapped[str] = mapped_column(String(30), index=True)
    operation: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_accuracy: Mapped[str] = mapped_column(String(20), default="unknown")
    derivation_version: Mapped[str] = mapped_column(String(30), default="node-v1")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    task_run: Mapped[TaskRun] = relationship(back_populates="nodes")
    parent: Mapped["ExecutionNode | None"] = relationship(remote_side=[id])
    chat_message: Mapped[ChatMessage | None] = relationship()
    tool_execution: Mapped[ToolExecution | None] = relationship()


class OutcomeEvidence(Base):
    __tablename__ = "outcome_evidence"
    __table_args__ = (UniqueConstraint("evidence_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("task_runs.id", ondelete="CASCADE"), index=True)
    execution_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("execution_nodes.id", ondelete="SET NULL"), nullable=True, index=True)
    memory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("memories.id", ondelete="SET NULL"), nullable=True, index=True)
    evidence_key: Mapped[str] = mapped_column(String(128))
    evidence_type: Mapped[str] = mapped_column(String(50), index=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    strength: Mapped[str] = mapped_column(String(20), index=True)
    source_type: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    independence_group: Mapped[str] = mapped_column(String(80), default="default")
    evaluator_version: Mapped[str] = mapped_column(String(40), default="rules-v1")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    task_run: Mapped[TaskRun] = relationship(back_populates="evidence")
    execution_node: Mapped[ExecutionNode | None] = relationship()
    memory: Mapped[Memory | None] = relationship()


class RecallEvent(Base):
    __tablename__ = "recall_events"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    external_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    query_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    result_scores: Mapped[list[dict]] = mapped_column(JSON, default=list)
    consumed_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    project: Mapped[Project | None] = relationship()
    task_run: Mapped[TaskRun | None] = relationship()


class PathScore(Base):
    __tablename__ = "path_scores"
    __table_args__ = (UniqueConstraint("task_run_id", "path_key", "derivation_version"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("task_runs.id", ondelete="CASCADE"), index=True)
    execution_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("execution_nodes.id", ondelete="SET NULL"), nullable=True, index=True)
    path_key: Mapped[str] = mapped_column(String(300))
    relatedness: Mapped[float] = mapped_column(Float, default=0.0)
    downstream_dependency: Mapped[float] = mapped_column(Float, default=0.0)
    novelty: Mapped[float] = mapped_column(Float, default=0.0)
    validation_value: Mapped[float] = mapped_column(Float, default=0.0)
    redundancy: Mapped[float] = mapped_column(Float, default=0.0)
    necessity: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance: Mapped[float | None] = mapped_column(Float, nullable=True)
    direct_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    critical_path_ms: Mapped[float] = mapped_column(Float, default=0.0)
    efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_method: Mapped[str] = mapped_column(String(30), default="observational")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    derivation_version: Mapped[str] = mapped_column(String(30), default="path-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    task_run: Mapped[TaskRun] = relationship(back_populates="path_scores")
    execution_node: Mapped[ExecutionNode | None] = relationship()


class EvaluationJob(Base):
    __tablename__ = "evaluation_jobs"
    __table_args__ = (UniqueConstraint("job_key"), Index("ix_evaluation_jobs_claim", "status", "created_at"))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("task_runs.id", ondelete="CASCADE"), index=True)
    job_key: Mapped[str] = mapped_column(String(200))
    job_type: Mapped[str] = mapped_column(String(50), default="evaluate_task")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task_run: Mapped[TaskRun] = relationship()


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="baseline", index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    automatic: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    project: Mapped[Project] = relationship()
    parent: Mapped["StrategyVersion | None"] = relationship(remote_side=[id])


class ExperimentAssignment(Base):
    __tablename__ = "experiment_assignments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("strategy_versions.id", ondelete="CASCADE"), index=True)
    baseline_strategy_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True)
    external_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arm: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(20), default="observe", index=True)
    assignment_probability: Mapped[float] = mapped_column(Float, default=1.0)
    adherence: Mapped[str] = mapped_column(String(20), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    task_run: Mapped[TaskRun | None] = relationship()
    strategy_version: Mapped[StrategyVersion] = relationship(foreign_keys=[strategy_version_id])
    baseline_strategy: Mapped[StrategyVersion | None] = relationship(foreign_keys=[baseline_strategy_id])


class StrategyEvaluation(Base):
    __tablename__ = "strategy_evaluations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_bucket: Mapped[str] = mapped_column(String(120), index=True)
    cases: Mapped[int] = mapped_column(Integer, default=0)
    strong_cases: Mapped[int] = mapped_column(Integer, default=0)
    quality_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_lcb: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_ucb: Mapped[float | None] = mapped_column(Float, nullable=True)
    severe_failure_rate: Mapped[float] = mapped_column(Float, default=0.0)
    average_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    average_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    promotion_allowed: Mapped[bool] = mapped_column(default=False)
    gate_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    strategy_version: Mapped[StrategyVersion | None] = relationship()
    project: Mapped[Project] = relationship()
