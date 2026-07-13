"""Add task runs, execution DAGs, outcome evidence, recall events, and path scores."""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.String(255), nullable=False),
        sa.Column("request_message_id", sa.Uuid(), nullable=True),
        sa.Column("response_message_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("task_kind", sa.String(40), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=True),
        sa.Column("reasoning_effort", sa.String(40), nullable=True),
        sa.Column("route_arm", sa.String(80), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=True),
        sa.Column("assignment_probability", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capture_completeness", sa.Float(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("quality_confidence", sa.Float(), nullable=False),
        sa.Column("quality_status", sa.String(30), nullable=False),
        sa.Column("severe_failure", sa.Boolean(), nullable=False),
        sa.Column("derivation_version", sa.String(30), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["request_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["response_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "turn_id"),
    )
    for name in ("project_id", "session_id", "turn_id", "status", "task_kind", "risk_level", "model_name", "route_arm", "policy_version", "started_at", "quality_status", "severe_failure"):
        op.create_index(f"ix_task_runs_{name}", "task_runs", [name])
    op.create_index("ix_task_runs_project_started", "task_runs", ["project_id", "started_at"])

    op.create_table(
        "execution_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_node_id", sa.Uuid(), nullable=True),
        sa.Column("chat_message_id", sa.Uuid(), nullable=True),
        sa.Column("tool_execution_id", sa.Uuid(), nullable=True),
        sa.Column("node_key", sa.String(300), nullable=False),
        sa.Column("branch_id", sa.String(255), nullable=True),
        sa.Column("agent_id", sa.String(255), nullable=True),
        sa.Column("node_type", sa.String(30), nullable=False),
        sa.Column("operation", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("cost_accuracy", sa.String(20), nullable=False),
        sa.Column("derivation_version", sa.String(30), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_node_id"], ["execution_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chat_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tool_execution_id"], ["tool_executions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_message_id"),
        sa.UniqueConstraint("tool_execution_id"),
        sa.UniqueConstraint("task_run_id", "node_key"),
    )
    for name in ("task_run_id", "branch_id", "agent_id", "node_type", "operation", "status"):
        op.create_index(f"ix_execution_nodes_{name}", "execution_nodes", [name])
    op.create_index("ix_execution_nodes_task_sequence", "execution_nodes", ["task_run_id", "sequence"])

    op.create_table(
        "outcome_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=False),
        sa.Column("execution_node_id", sa.Uuid(), nullable=True),
        sa.Column("memory_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_key", sa.String(128), nullable=False),
        sa.Column("evidence_type", sa.String(50), nullable=False),
        sa.Column("metric", sa.String(80), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("strength", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("independence_group", sa.String(80), nullable=False),
        sa.Column("evaluator_version", sa.String(40), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_node_id"], ["execution_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_key"),
    )
    for name in ("task_run_id", "execution_node_id", "memory_id", "evidence_type", "metric", "strength", "created_at"):
        op.create_index(f"ix_outcome_evidence_{name}", "outcome_evidence", [name])

    op.create_table(
        "recall_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("task_run_id", sa.Uuid(), nullable=True),
        sa.Column("external_session_id", sa.String(200), nullable=True),
        sa.Column("turn_id", sa.String(255), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=True),
        sa.Column("result_ids", sa.JSON(), nullable=False),
        sa.Column("result_scores", sa.JSON(), nullable=False),
        sa.Column("consumed_ids", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for name in ("project_id", "task_run_id", "external_session_id", "turn_id", "query_hash", "created_at"):
        op.create_index(f"ix_recall_events_{name}", "recall_events", [name])

    op.create_table(
        "path_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=False),
        sa.Column("execution_node_id", sa.Uuid(), nullable=True),
        sa.Column("path_key", sa.String(300), nullable=False),
        sa.Column("relatedness", sa.Float(), nullable=False),
        sa.Column("downstream_dependency", sa.Float(), nullable=False),
        sa.Column("novelty", sa.Float(), nullable=False),
        sa.Column("validation_value", sa.Float(), nullable=False),
        sa.Column("redundancy", sa.Float(), nullable=False),
        sa.Column("necessity", sa.Float(), nullable=True),
        sa.Column("importance", sa.Float(), nullable=True),
        sa.Column("direct_cost_usd", sa.Float(), nullable=False),
        sa.Column("critical_path_ms", sa.Float(), nullable=False),
        sa.Column("efficiency", sa.Float(), nullable=True),
        sa.Column("evidence_method", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("derivation_version", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_node_id"], ["execution_nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_run_id", "path_key", "derivation_version"),
    )
    op.create_index("ix_path_scores_task_run_id", "path_scores", ["task_run_id"])
    op.create_index("ix_path_scores_execution_node_id", "path_scores", ["execution_node_id"])

    op.create_table(
        "evaluation_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=False),
        sa.Column("job_key", sa.String(200), nullable=False),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_key"),
    )
    op.create_index("ix_evaluation_jobs_task_run_id", "evaluation_jobs", ["task_run_id"])
    op.create_index("ix_evaluation_jobs_status", "evaluation_jobs", ["status"])
    op.create_index("ix_evaluation_jobs_claim", "evaluation_jobs", ["status", "created_at"])


def downgrade():
    for table in ("evaluation_jobs", "path_scores", "recall_events", "outcome_evidence", "execution_nodes", "task_runs"):
        op.drop_table(table)
