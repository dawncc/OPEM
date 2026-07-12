"""Seed a local running server with a small UTF-8 demonstration session."""
import httpx

SERVER = "http://127.0.0.1:8000"
ITEMS = [
    {
        "content": "\u51b3\u5b9a\u672c\u9879\u76ee\u7684\u672c\u673a\u6f14\u793a\u4f7f\u7528 SQLite \u6570\u636e\u5e93\u548c Python \u865a\u62df\u73af\u5883\uff0c\u4e0d\u4f9d\u8d56 Docker\u3002\u751f\u4ea7\u73af\u5883\u4ecd\u53ef\u5207\u6362 PostgreSQL\u3002",
        "kind": "decision", "project": "codex-lan-memory", "session_id": "local-demo-001",
        "concepts": ["SQLite", "Python", "\u672c\u673a\u90e8\u7f72"],
        "files": ["README.md", "packages/memory-server/src/memory_server/models.py"],
        "importance": 5, "idempotency_key": "demo-decision-sqlite-v3",
    },
    {
        "content": "\u672c\u673a\u6ca1\u6709 Docker \u548c PostgreSQL\u3002\u901a\u8fc7 SQLAlchemy \u7c7b\u578b\u7684 SQLite variant\uff0c\u53ef\u4ee5\u5728\u4fdd\u7559 PostgreSQL \u4e0e pgvector \u751f\u4ea7\u6a21\u578b\u7684\u540c\u65f6\u5feb\u901f\u8fd0\u884c\u672c\u673a\u6f14\u793a\u3002",
        "kind": "solution", "project": "codex-lan-memory", "session_id": "local-demo-001",
        "concepts": ["SQLAlchemy", "SQLite", "pgvector"],
        "files": ["packages/memory-server/src/memory_server/models.py"],
        "importance": 4, "idempotency_key": "demo-solution-sqlite-v3",
    },
    {
        "content": "\u5df2\u5b8c\u6210\u672c\u673a SQLite \u521d\u59cb\u5316\u3001FastAPI \u548c Worker \u542f\u52a8\u3002\n- \u5065\u5eb7\u68c0\u67e5\u6b63\u5e38\n- 5 \u9879\u6d4b\u8bd5\u901a\u8fc7\n- Memory Server \u76d1\u542c 8000 \u7aef\u53e3",
        "kind": "session_summary", "project": "codex-lan-memory", "session_id": "local-demo-001",
        "concepts": ["MVP", "\u672c\u673a\u6f14\u793a"], "files": ["demo.db"],
        "importance": 4, "idempotency_key": "demo-session-end-v3",
    },
]


if __name__ == "__main__":
    for item in ITEMS:
        response = httpx.post(f"{SERVER}/api/v1/observations", json=item, timeout=10)
        response.raise_for_status()
        print(response.json())

