"""Create the initial Memory schema from the declared SQLAlchemy metadata."""
from alembic import op
from memory_server.db import Base
from memory_server import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind())
    op.execute("CREATE INDEX IF NOT EXISTS ix_memories_search_fts ON memories USING gin (to_tsvector('simple', search_text))")


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute("DROP EXTENSION IF EXISTS vector")
