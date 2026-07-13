from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from memory_common.schemas import ChatBatchCreate
from memory_server.db import Base
from memory_server.main import tools_page
from memory_server.services import create_chat_batch


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/tools",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    })


def test_grouped_tools_page_limits_dom_payload_and_clips_long_text(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tools-page.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(engine, expire_on_commit=False)
    started = datetime.now(timezone.utc)
    messages = []
    for index in range(3):
        messages.append({
            "role": "tool",
            "content": f"pytest result {index}\n" + ("x" * 5000) + f"TAIL-{index}",
            "sequence": index,
            "created_at": started + timedelta(seconds=index),
            "metadata": {
                "tool_name": "shell",
                "tool_input": f"pytest tests/test_{index}.py -q " + ("y" * 2500),
                "status": "success",
            },
        })

    with SessionFactory() as db:
        create_chat_batch(db, ChatBatchCreate(
            project="tools-page",
            session_id="tools-page-session",
            messages=messages,
        ))
        response = tools_page(
            _request(), status=None, q="", view="grouped", family=None,
            language=None, tag=None, min_similarity=0, db=db,
        )

    html = response.body.decode("utf-8")
    assert response.status_code == 200
    assert html.count('<article class="group-execution success">') == 2
    assert "本组另有 1 次调用未在首屏展开" in html
    assert "内容较长，已截断" in html
    assert "TAIL-2" not in html
    assert len(response.body) < 65_000
