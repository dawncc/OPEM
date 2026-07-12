"""Add daily memory summaries."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "daily_summaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("memory_count", sa.Integer(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("learnings", sa.JSON(), nullable=False),
        sa.Column("unresolved", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "summary_date"),
    )
    op.create_index("ix_daily_summaries_project_id", "daily_summaries", ["project_id"])
    op.create_index("ix_daily_summaries_summary_date", "daily_summaries", ["summary_date"])


def downgrade():
    op.drop_table("daily_summaries")
