"""Archive tool executions and link derived observations."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_message_id", sa.Uuid(), sa.ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("observation_id", sa.Uuid(), sa.ForeignKey("observations.id", ondelete="SET NULL"), unique=True),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_text", sa.Text()),
        sa.Column("output_text", sa.Text(), nullable=False),
        sa.Column("error_reason", sa.Text()),
        sa.Column("error_category", sa.String(50)),
        sa.Column("evidence", sa.Text()),
        sa.Column("failure_signature", sa.String(64)),
        sa.Column("faq_question", sa.Text()),
        sa.Column("faq_answer", sa.Text()),
        sa.Column("derivation_version", sa.String(20), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_executions_project_id", "tool_executions", ["project_id"])
    op.create_index("ix_tool_executions_session_id", "tool_executions", ["session_id"])
    op.create_index("ix_tool_executions_tool_name", "tool_executions", ["tool_name"])
    op.create_index("ix_tool_executions_status", "tool_executions", ["status"])
    op.create_index("ix_tool_executions_error_category", "tool_executions", ["error_category"])
    op.create_index("ix_tool_executions_failure_signature", "tool_executions", ["failure_signature"])
    op.create_index("ix_tool_executions_created_at", "tool_executions", ["created_at"])


def downgrade():
    op.drop_table("tool_executions")
