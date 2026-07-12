# Codex LAN Memory

Python MVP for submitting durable Codex observations over MCP, consolidating them into memories, recalling them with RAG-style search, and browsing them on a small LAN web UI.

## Start the server

```bash
cp .env.example .env
cd deploy
docker compose up --build -d
```

Open `http://<server-lan-ip>:8000`. The database is not exposed outside Docker.

### Local SQLite demo without Docker

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
$env:DATABASE_URL="sqlite:///./demo.db"
.\.venv\Scripts\python -c "from memory_server.db import Base,engine; import memory_server.models; Base.metadata.create_all(engine)"
# Run these in separate terminals with the same DATABASE_URL:
.\.venv\Scripts\codex-memory-server
.\.venv\Scripts\codex-memory-worker
# Optionally seed a demonstration session:
.\.venv\Scripts\python scripts\seed_demo.py
```

## Install the MCP client on each Codex machine

Install Python 3.12, then from a checkout of this repository:

```bash
pipx install .
```

Add this to `$CODEX_HOME/config.toml`:

```toml
[mcp_servers.codex_memory]
command = "codex-memory-mcp"
args = ["--server", "http://192.168.1.100:8000"]
startup_timeout_sec = 30
```

Copy `skill/codex-memory` into `$CODEX_HOME/skills/codex-memory`. Restart Codex and call `memory_health` to verify connectivity.

When Codex calls `memory_recall`, the response includes both structured matches and a `<chat-memory>` block. The block is immediately visible to Codex as tool context, carries source session information, and explicitly marks recalled content as historical evidence rather than instructions.

### Hybrid retrieval

Recall combines exact-phrase matching, field-weighted BM25, typo-tolerant fuzzy matching, concept/file metadata, and optional embedding similarity. English/Chinese aliases, Chinese bigrams, and file-path segments improve cross-language and path recall. Weighted reciprocal-rank fusion chooses candidates, calibrated content relevance is shown to users, and MMR removes near-duplicate results. `MEMORY_RECALL_CANDIDATE_LIMIT=0` searches every Memory in the selected project, which is the recommended small-team default. At larger scale, set a bounded candidate pool and move first-stage top-K retrieval to SQLite FTS5 or PostgreSQL GIN/pgvector indexes.

`memory_chat_submit` stores the complete chronological transcript separately from derived Memory. Session pages show the full user/assistant/system/tool content first, then observations and summarized memories. Re-sending the same session sequence is idempotent.

Session pages group messages into user-led turns. Each turn is classified as conversation, code, tool, or error; fenced code is rendered in a dedicated code panel and tool execution is rendered as a terminal-style card with normalized status and scrollable full output. Turn navigation expands collapsed rounds automatically, while all stored text remains HTML-escaped.

### Tool execution archive and FAQ

Every `tool` message submitted through `memory_chat_submit` is archived with its complete input metadata and raw output. Structured metadata takes precedence when normalizing the status to `success`, `failed`, or `unknown`:

- Clear successes create a `learning` observation and enter the Worker/Recall pipeline as `tool_success` historical context.
- Failures create an evidence-backed `problem` observation and a separate `tool_failure_faq` Memory with an FAQ question, extracted reason, error category, remediation suggestion, and stable failure signature.
- Consolidation only compares candidates of the same Memory type, so similar success output and failure output can never be merged into one record.
- Unknown results remain visible in the raw archive but are not promoted to Memory until their outcome is known.

Open `/tools` to filter all executions and `/faq` to browse failed-tool knowledge with links back to the complete source session. Tool-message sequence numbers and archive IDs are idempotent, so retransmitting a chat batch does not duplicate records.

## Daily summaries and calendar

The worker refreshes project-by-day summaries every 60 seconds by default. Configure the grouping timezone with `MEMORY_TIMEZONE` and the interval with `MEMORY_DAILY_REFRESH_SECONDS`. Open `/calendar` to browse a month and click a date to view decisions, solutions, unresolved problems, and Memory highlights for that day.

## Test from a remote Codex host

If the remote host cannot route to the Memory Server LAN address, create an SSH reverse tunnel from the Memory Server machine:

```bash
ssh -N -R 18000:127.0.0.1:8000 <remote-host>
```

On the remote host, install the wheel into a virtual environment or user target and configure Codex to launch the stdio bridge with `--server http://127.0.0.1:18000`. `scripts/test_remote_codex.sh` performs an end-to-end Codex Agent health, successful/failed tool archive, FAQ, and recall test. A directly routable HTTP/VPN address is preferred for permanent deployment; the reverse tunnel exists only while its SSH process is running.

## Optional session-end hook

Set `MEMORY_SERVER_URL`, ensure `httpx` is available to the hook Python, then register `hooks/session-end.py` for Codex `Stop` and `PreCompact`. It accepts hook JSON on stdin. If the server is unavailable, it appends the summary to `$CODEX_HOME/codex-memory/pending.jsonl`; the MCP client flushes that file on its next call.

## Optional local models

- Set `MEMORY_EMBEDDING_ENABLED=true` and install `.[embedding]` in the worker image to create local embeddings.
- Set `MEMORY_LLM_ENABLED=true` and configure an OpenAI-compatible `/v1/chat/completions` endpoint for structured compression.
- Both are optional. The default worker uses deterministic rule compression and search falls back to PostgreSQL text matching.

## Development

```bash
pip install -e ".[dev]"
pytest
```
