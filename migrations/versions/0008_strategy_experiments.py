"""Add versioned execution strategies and bounded experiments."""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("metrics_snapshot", sa.JSON(), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["strategy_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version"),
    )
    op.create_index("ix_strategy_versions_project_id", "strategy_versions", ["project_id"])
    op.create_index("ix_strategy_versions_status", "strategy_versions", ["status"])

    op.create_table(
        "experiment_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_strategy_id", sa.Uuid(), nullable=True),
        sa.Column("external_session_id", sa.String(200), nullable=True),
        sa.Column("turn_id", sa.String(255), nullable=True),
        sa.Column("arm", sa.String(80), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("assignment_probability", sa.Float(), nullable=False),
        sa.Column("adherence", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["baseline_strategy_id"], ["strategy_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in ("task_run_id", "strategy_version_id", "external_session_id", "turn_id", "mode", "created_at"):
        op.create_index(f"ix_experiment_assignments_{name}", "experiment_assignments", [name])

    op.create_table(
        "strategy_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_bucket", sa.String(120), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("strong_cases", sa.Integer(), nullable=False),
        sa.Column("quality_mean", sa.Float(), nullable=True),
        sa.Column("quality_lcb", sa.Float(), nullable=True),
        sa.Column("quality_ucb", sa.Float(), nullable=True),
        sa.Column("severe_failure_rate", sa.Float(), nullable=False),
        sa.Column("average_cost_usd", sa.Float(), nullable=False),
        sa.Column("average_latency_ms", sa.Float(), nullable=False),
        sa.Column("promotion_allowed", sa.Boolean(), nullable=False),
        sa.Column("gate_reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in ("strategy_version_id", "project_id", "task_bucket", "created_at"):
        op.create_index(f"ix_strategy_evaluations_{name}", "strategy_evaluations", [name])


def downgrade():
    op.drop_table("strategy_evaluations")
    op.drop_table("experiment_assignments")
    op.drop_table("strategy_versions")
