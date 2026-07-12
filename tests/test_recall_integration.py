from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from memory_server.db import Base
from memory_server.models import Memory, Project
from memory_server.services import recall


def memory(title, content, concepts=None, files=None):
    return Memory(
        title=title, content=content, memory_type="learning", concepts=concepts or [], files=files or [],
        importance=4, confidence=0.8, search_text=" ".join([title, content, *(concepts or []), *(files or [])]),
    )


def seeded_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(name="demo")
    other = Project(name="other")
    session.add_all([project, other])
    session.flush()
    records = [
        memory("PostgreSQL vector storage", "Use pgvector for semantic retrieval.", ["pgvector", "RAG"], ["memory_server/services.py"]),
        memory("Remote Codex transcript", "A remote Codex server stored its complete chat transcript through MCP.", ["MCP", "remote"]),
        memory("Weather notes", "Tomorrow will be sunny.", ["weather"]),
    ]
    for record in records:
        record.project_id = project.id
    hidden = memory("PostgreSQL secret project", "Should remain isolated from demo.", ["postgresql"])
    hidden.project_id = other.id
    session.add_all([*records, hidden])
    session.commit()
    return session


def test_hybrid_recall_handles_aliases_paths_and_calibrated_scores():
    session = seeded_session()
    vector_results = recall(session, "Postgres 向量检索", "demo", 5)
    assert vector_results[0].title == "PostgreSQL vector storage"
    assert vector_results[0].score < 0.9
    assert "BM25" in vector_results[0].matched_by
    path_results = recall(session, "services.py", "demo", 5)
    assert path_results[0].title == "PostgreSQL vector storage"


def test_hybrid_recall_cross_language_and_negative_query():
    session = seeded_session()
    results = recall(session, "远程服务器完整聊天", "demo", 5)
    assert results[0].title == "Remote Codex transcript"
    assert recall(session, "量子化学蛋白质天气", "demo", 5) == []


def test_hybrid_recall_keeps_project_isolation():
    session = seeded_session()
    titles = [item.title for item in recall(session, "Postgres", "demo", 10)]
    assert "PostgreSQL secret project" not in titles
