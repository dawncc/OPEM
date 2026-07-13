"""Create the initial Memory schema from the declared SQLAlchemy metadata."""
from alembic import op
from memory_server.db import Base
from memory_server import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Create only the schema that belonged to revision 0001. Calling
    # Base.metadata.create_all() here would also create tables introduced by
    # later revisions and make a clean 0001 -> head migration impossible.
    for name in ("projects", "sessions", "observations", "memories", "memory_sources", "processing_jobs"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        op.execute("CREATE INDEX IF NOT EXISTS ix_memories_search_fts ON memories USING gin (to_tsvector('simple', search_text))")


def downgrade():
    bind = op.get_bind()
    for name in ("processing_jobs", "memory_sources", "memories", "observations", "sessions", "projects"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS vector")
