from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from memory_server.db import Base
from memory_server.main import memories_page, memory_page, search_page
from memory_server.models import Memory, MemorySource, Observation, Project


def _request(path: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    })


def test_memories_page_defaults_to_latest_and_links_to_detail(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'memories-page.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with SessionFactory() as db:
        project = Project(name="page-test")
        db.add(project)
        db.flush()
        older = Memory(
            project_id=project.id, title="较早的记忆", content="旧内容",
            memory_type="learning", search_text="较早的记忆 旧内容",
            updated_at=now - timedelta(days=1),
        )
        latest = Memory(
            project_id=project.id, title="最新的记忆", content="结论\n应默认展示最新内容",
            memory_type="decision", search_text="最新的记忆 默认展示",
            updated_at=now,
        )
        db.add_all([older, latest])
        db.flush()
        observation = Observation(
            project_id=project.id, kind="decision", content="应默认展示最新内容",
            concepts=[], files=[], importance=4,
        )
        db.add(observation)
        db.flush()
        db.add(MemorySource(memory_id=latest.id, observation_id=observation.id, relation="derived_from"))
        db.commit()

        response = memories_page(_request("/memories"), q="", project=None, db=db)
        html = response.body.decode("utf-8")
        assert response.status_code == 200
        assert "最新记忆" in html
        assert html.index("最新的记忆") < html.index("较早的记忆")
        assert f'href="/memories/{latest.id}"' in html

        detail = memory_page(_request(f"/memories/{latest.id}"), latest.id, db=db)
        detail_html = detail.body.decode("utf-8")
        assert "能力与结果" in detail_html
        assert "功能对象" in detail_html
        assert "实际能力" in detail_html
        assert "关键动作" in detail_html
        assert "待确认" in detail_html
        assert "记忆总结" in detail_html
        assert '<h3>结论</h3>' in detail_html
        assert "来源与证据" in detail_html
        assert "应默认展示最新内容" in detail_html


def test_legacy_search_redirects_to_memories():
    response = search_page(q="SQLite", project="demo")
    assert response.status_code == 307
    assert response.headers["location"] == "/memories?q=SQLite&project=demo"
