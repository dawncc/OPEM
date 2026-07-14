"""Add structured display details to memories."""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memories", sa.Column("subject", sa.String(200), nullable=True))
    op.add_column("memories", sa.Column("capability", sa.Text(), nullable=True))
    op.add_column("memories", sa.Column("action", sa.Text(), nullable=True))
    op.add_column("memories", sa.Column("outcome", sa.Text(), nullable=True))
    op.add_column("memories", sa.Column("outcome_status", sa.String(20), nullable=False, server_default="unknown"))


def downgrade():
    op.drop_column("memories", "outcome_status")
    op.drop_column("memories", "outcome")
    op.drop_column("memories", "action")
    op.drop_column("memories", "capability")
    op.drop_column("memories", "subject")
