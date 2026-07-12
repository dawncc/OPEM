"""Add stable event ids for idempotent real-time hook ingestion."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_messages", sa.Column("event_id", sa.String(255), nullable=True))
    op.create_index("ix_chat_messages_event_id", "chat_messages", ["event_id"])
    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.create_unique_constraint("uq_chat_messages_session_event", ["session_id", "event_id"])


def downgrade():
    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.drop_constraint("uq_chat_messages_session_event", type_="unique")
    op.drop_index("ix_chat_messages_event_id", table_name="chat_messages")
    op.drop_column("chat_messages", "event_id")
