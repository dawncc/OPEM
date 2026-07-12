"""Add auditable recall feedback for bounded memory evolution."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(200), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_memory_feedback_memory_id", "memory_feedback", ["memory_id"])
    op.create_index("ix_memory_feedback_outcome", "memory_feedback", ["outcome"])
    op.create_index("ix_memory_feedback_session_id", "memory_feedback", ["session_id"])
    op.create_index("ix_memory_feedback_created_at", "memory_feedback", ["created_at"])


def downgrade():
    op.drop_table("memory_feedback")
