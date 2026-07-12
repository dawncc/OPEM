# Onevom

**One Personal Evolving Memory System**

**English** | [简体中文](README.zh-CN.md)

[Live Sites showcase](https://codex-lan-memory.dawn-cc022.chatgpt.site) · Owner-only deployment

Onevom, short for **One Personal Evolving Memory System**, aggregates conversations, durable observations, and tool outcomes from Codex instances across multiple machines, consolidates them into long-term knowledge, supports contextual recall, and evolves as its owner continues to use it.

> MVP scope: trusted LAN HTTP, one shared server, SQLite or PostgreSQL, no authentication or TLS.

![Onevom overview](docs/images/overview.png)

## Highlights

- **Cross-machine collection** — multiple Codex CLI, Desktop, or Remote hosts write to one Memory Server.
- **MCP-first integration** — submit, recall, feed results back, end sessions, archive complete chat, and check health through six MCP tools.
- **Raw history plus durable Memory** — the complete transcript remains available while reusable knowledge is compressed separately.
- **Hybrid recall** — exact phrase, BM25, fuzzy, metadata, and optional vector similarity are fused with weighted RRF and deduplicated with MMR.
- **Session Trace** — group each Session into user-led request paths and show Codex/tool nodes with measured or estimated timing.
- **Tool archive and FAQ** — successful tools become recallable context; failed tools become evidence-backed FAQ entries.
- **Daily summaries** — browse decisions, learnings, unresolved problems, and Memory highlights on a calendar.
- **Small deployment footprint** — FastAPI, SQLAlchemy, one Worker, server-rendered pages, and SQLite or PostgreSQL/pgvector.

## Screenshots

### Session execution trace

Request paths connect user messages, Codex responses, and tool calls. Test, shell, file, search, browser, network/MCP, database, and code tools use distinct styles.

![Session execution trace](docs/images/session-trace.png)

### Tool execution archive

Every tool result is archived. Clear successes enter the Memory pipeline, failures enter the FAQ pipeline, and unknown outcomes remain raw-only.

![Tool execution archive](docs/images/tool-archive.png)

### Failure FAQ

Failures retain the observed evidence, normalized category, remediation suggestion, stable signature, and a link to the source Session.

![Failure FAQ](docs/images/failure-faq.png)

## Architecture

```mermaid
flowchart LR
    subgraph Clients[Codex machines]
        Codex[Codex CLI / Desktop / Remote]
        Skill[Memory Skill]
        MCP[Python stdio MCP bridge]
        Queue[Local pending JSONL]
        Codex --> Skill --> MCP
        MCP --> Queue
    end

    MCP -->|LAN HTTP| API

    subgraph Server[Shared Memory Server]
        API[FastAPI]
        DB[(SQLite or PostgreSQL + pgvector)]
        Worker[Memory Worker]
        Memory[Consolidated Memory]
        UI[Jinja2 Web UI]
        API --> DB
        DB --> Worker --> Memory
        DB --> UI
        Memory --> UI
    end

    Memory -->|hybrid recall| API
    API -->|chat-memory context| MCP
```

The server keeps raw `ChatMessage` and `Observation` records independent from generated `Memory`. Every generated Memory retains source Observation and Session links.

## Quick start: local SQLite

Requirements: Python 3.12 or newer.

```bash
git clone git@github.com:dawncc/codex-lan-memory.git
cd codex-lan-memory
python -m venv .venv
```

Activate the environment and install the project:

```bash
# Linux / macOS
source .venv/bin/activate
pip install -e ".[dev]"

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Initialize SQLite:

```bash
# Linux / macOS
export DATABASE_URL="sqlite:///./demo.db"
python -c "from memory_server.db import Base,engine; import memory_server.models; Base.metadata.create_all(engine)"
```

```powershell
# Windows PowerShell
$env:DATABASE_URL="sqlite:///./demo.db"
.\.venv\Scripts\python -c "from memory_server.db import Base,engine; import memory_server.models; Base.metadata.create_all(engine)"
```

Run these in two terminals with the same `DATABASE_URL`:

```bash
# Linux / macOS
codex-memory-server
codex-memory-worker
```

```powershell
# Windows PowerShell
.\.venv\Scripts\codex-memory-server
.\.venv\Scripts\codex-memory-worker
```

Optionally load demonstration data:

```powershell
.\.venv\Scripts\python scripts\seed_demo.py
.\.venv\Scripts\python scripts\seed_conversation_demo.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Deploy with Docker Compose

The Compose deployment starts PostgreSQL/pgvector, the API/UI server, and the Worker.

```bash
cp .env.example .env
cd deploy
docker compose up --build -d
```

Open `http://<server-lan-ip>:8000`. PostgreSQL is only exposed to the internal Compose network.

## Connect Codex

Install the package on every Codex machine:

```bash
pipx install .
```

Add the MCP server to `$CODEX_HOME/config.toml`:

```toml
[mcp_servers.codex_memory]
command = "codex-memory-mcp"
args = ["--server", "http://192.168.1.100:8000"]
startup_timeout_sec = 30
```

Copy `skill/codex-memory` to `$CODEX_HOME/skills/codex-memory`, restart Codex, and call `memory_health`.

The Skill instructs Codex to:

1. recall relevant project history before substantial work;
2. save durable decisions, learnings, problems, and solutions;
3. archive the complete conversation and all available tool results;
4. include `duration_ms` when exact timing is available;
5. submit a structured summary before session end or compaction.
6. report whether recalled memories were helpful, irrelevant, or harmful.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `memory_submit` | Save a durable observation, decision, learning, problem, solution, or session summary. |
| `memory_recall` | Return structured matches and a prompt-ready `<chat-memory>` context block. |
| `memory_feedback` | Record recall utility and calibrate future ranking confidence. |
| `memory_session_end` | Submit completed work, decisions, unresolved items, and files. |
| `memory_chat_submit` | Store the complete chronological user/assistant/system/tool transcript. |
| `memory_health` | Check the server/database and flush the local pending queue. |

## HTTP surface

The MVP keeps the business API intentionally small:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/observations` | Receive durable observations and session summaries. |
| `POST` | `/api/v1/chat/messages` | Receive complete chat batches and tool metadata. |
| `POST` | `/api/v1/recall` | Run hybrid Memory retrieval. |
| `POST` | `/api/v1/memories/feedback` | Record auditable recall feedback and evolve confidence. |
| `GET` | `/api/v1/health` | Check database health and pending jobs. |

Main UI routes:

- `/` — overview
- `/search` — Memory search
- `/calendar` — daily Memory calendar
- `/traces` — Session request paths and timing
- `/tools` — tool execution archive
- `/faq` — failed-tool FAQ
- `/sessions/<id>` — complete conversation

## Memory lifecycle

1. **Capture** — save the raw Observation or complete chat first.
2. **Compress** — use an optional OpenAI-compatible model, or deterministic rule compression when no LLM is configured.
3. **Consolidate** — merge only compatible Memory types and keep every source link.
4. **Retrieve** — combine exact phrase, BM25, fuzzy, metadata, and optional embedding results.

Successful tools become `tool_success` Memory. Failed tools become separate `tool_failure_faq` Memory so similar success and failure output can never be consolidated together. Unknown outcomes are archived without promotion.

Recall outcomes form a bounded evolution loop: explicit feedback calibrates ranking confidence without changing application code or model weights. See [the self-evolution design](docs/self-evolution.md) for the safety boundary and roadmap.

## Trace timing

Pass top-level `duration_ms` on a `memory_chat_submit` message when exact timing is known. Trace renders exact timing in blue. Older messages fall back to the adjacent message timestamp and are explicitly marked as estimates. Active duration excludes idle time between separate user requests.

## Remote Codex hosts

If a remote host cannot route directly to the LAN server, create a reverse SSH tunnel from the Memory Server machine:

```bash
ssh -N -R 18000:127.0.0.1:8000 <remote-host>
```

Configure the remote MCP bridge with `--server http://127.0.0.1:18000`. The included `scripts/test_remote_codex.sh` validates health, chat submission, successful/failed tool archival, FAQ generation, and recall from a real remote Codex process.

A directly routable LAN, VPN, or private network address is preferred for permanent deployment; the reverse tunnel only exists while its SSH process is running.

## Configuration

| Variable | Default / example | Description |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://memory:memory@postgres/memory` | SQLAlchemy database URL; use `sqlite:///./demo.db` locally. |
| `MEMORY_SERVER_HOST` | `0.0.0.0` | API bind address. |
| `MEMORY_SERVER_PORT` | `8000` | API/UI port. |
| `MEMORY_LLM_ENABLED` | `false` | Enable OpenAI-compatible compression. |
| `MEMORY_LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint. |
| `MEMORY_LLM_MODEL` | `qwen3` | Compression model name. |
| `MEMORY_EMBEDDING_ENABLED` | `false` | Enable local embeddings. |
| `MEMORY_EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | Sentence-transformers model. |
| `MEMORY_RECALL_CANDIDATE_LIMIT` | `0` | `0` searches every Memory; use a limit for larger datasets. |
| `MEMORY_DAILY_REFRESH_SECONDS` | `60` | Daily-summary refresh interval. |
| `MEMORY_TIMEZONE` | `Asia/Shanghai` | Daily-summary grouping timezone. |

## Real-time Codex hooks (recommended)

MCP/Skill-only capture depends on Codex choosing to call a tool and can miss ordinary requests. Install deterministic lifecycle hooks on every Codex host:

```bash
python scripts/install_codex_hooks.py --server http://192.168.1.100:8000
```

The installer preserves existing hooks and adds `UserPromptSubmit`, `PostToolUse`, `Stop`, and `PreCompact` capture. User prompts, tool results, and final assistant messages become visible immediately; `Stop` also creates an Observation for asynchronous Memory consolidation. The standard-library-only client queues failed deliveries under `$CODEX_HOME/codex-memory/pending.jsonl` and retries them on the next lifecycle event.

After installation, open `/hooks` in Codex, review and trust the new definitions, then start a new task. The home page refreshes its live-session list every five seconds. `hooks/session-end.py` remains only as a legacy compatibility entry point.

### Backfill completed local chats

Preview completed turns stored under `$CODEX_HOME/sessions`, then synchronize them:

```bash
codex-memory-sync --server http://192.168.1.100:8000 --since 2026-07-01 --dry-run
codex-memory-sync --server http://192.168.1.100:8000 --since 2026-07-01
```

Only turns with a `task_complete` event are imported. The backfill uses the same turn, tool-call, and event identifiers as the real-time hooks, so reruns and hook/backfill overlap are idempotent. Legacy rows without event identifiers use role/content occurrence matching as a compatibility fallback. Rollout JSONL remains a best-effort local import source; real-time hooks are the supported primary capture path.

## Design notes

- [Cost control and context compression for multi-turn tasks (Chinese)](docs/context-cost-compression.md)

## Project layout

```text
packages/memory-common/   Shared schemas, settings, pending queue
packages/memory-server/   FastAPI, SQLAlchemy, recall, Trace, Jinja2 UI
packages/memory-worker/   compression, consolidation, embeddings, daily summaries
packages/memory-mcp/      local stdio MCP-to-HTTP bridge
skill/codex-memory/       Codex workflow Skill
hooks/                    Stop / PreCompact fallback hook
migrations/               Alembic migrations
deploy/                   Docker Compose deployment
scripts/                  seed and remote validation scripts
tests/                    unit and integration tests
docs/images/              README screenshots
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Current test coverage includes chat preservation, Chinese/English retrieval, recall isolation, pending retries, tool outcome classification, FAQ construction, Worker consolidation boundaries, Trace timing, and HTML escaping.

## Security scope

This MVP assumes a trusted private network. It does **not** implement TLS, authentication, device tokens, tenant isolation, or automatic redaction. Add those controls before exposing the service beyond a trusted LAN or VPN.

## License

Copyright (c) 2026 dawncc.

This project is open-source software licensed under the [MIT License](LICENSE). You may use, copy, modify, merge, publish, distribute, sublicense, and sell copies subject to the license terms.
