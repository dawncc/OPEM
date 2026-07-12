# Codex 局域网记忆系统

[English](README.md) | **简体中文**

[Sites 在线展示](https://codex-lan-memory.dawn-cc022.chatgpt.site) · 仅所有者可访问

一套轻量、可自行部署的 Codex 记忆与执行链路系统。不同机器上的 Codex CLI、Desktop 或 Remote 实例通过本地 MCP 桥接进程提交完整对话、长期观察和工具执行结果；统一的 Python 服务负责保存原始历史、归并长期 Memory、执行混合召回，并提供简洁的网页界面。

> MVP 边界：可信局域网 HTTP、单台共享服务器、SQLite 或 PostgreSQL，暂不包含鉴权与 TLS。

![Codex 局域网记忆系统概览](docs/images/overview.png)

## 核心能力

- **跨机器汇总**：多台 Codex CLI、Desktop 或 Remote 主机共同写入一台 Memory Server。
- **以 MCP 为核心**：通过五个 MCP 工具完成提交、召回、会话结束、完整聊天归档和健康检查。
- **原始历史与长期记忆分离**：完整聊天长期保留，可复用知识单独压缩为 Memory。
- **混合召回**：融合精确短语、BM25、模糊匹配、元数据和可选向量相似度，并通过加权 RRF 排序、MMR 去重。
- **Session Trace**：按用户请求拆分执行路径，展示 Codex 与工具节点，以及精确或估算耗时。
- **工具归档与 FAQ**：成功工具可成为召回上下文；失败工具自动生成带证据的 FAQ。
- **每日记忆日历**：按天查看决策、经验、未解决问题和 Memory 摘要。
- **部署组件少**：FastAPI、SQLAlchemy、单个 Worker、服务端页面，以及 SQLite 或 PostgreSQL/pgvector。

## 系统截图

### Session 执行链路

每条路径连接用户请求、Codex 响应和工具调用。测试、命令行、文件、搜索、浏览器、网络/MCP、数据库和代码执行采用不同样式。

![Session 执行链路](docs/images/session-trace.png)

### 工具执行归档

所有工具结果都会保存。明确成功的结果进入 Memory 流程，失败结果进入 FAQ 流程，状态未知的结果只保留原始归档。

![工具执行归档](docs/images/tool-archive.png)

### 失败 FAQ

FAQ 保留已观察到的证据、标准化错误分类、处理建议、稳定失败签名，以及原始 Session 跳转。

![失败 FAQ](docs/images/failure-faq.png)

## 系统架构

```mermaid
flowchart LR
    subgraph Clients[Codex 客户端机器]
        Codex[Codex CLI / Desktop / Remote]
        Skill[Memory Skill]
        MCP[Python stdio MCP 桥接]
        Queue[本地 pending JSONL]
        Codex --> Skill --> MCP
        MCP --> Queue
    end

    MCP -->|局域网 HTTP| API

    subgraph Server[统一 Memory 服务器]
        API[FastAPI]
        DB[(SQLite 或 PostgreSQL + pgvector)]
        Worker[Memory Worker]
        Memory[归并后的长期 Memory]
        UI[Jinja2 网页]
        API --> DB
        DB --> Worker --> Memory
        DB --> UI
        Memory --> UI
    end

    Memory -->|混合召回| API
    API -->|chat-memory 上下文| MCP
```

服务端将原始 `ChatMessage`、`Observation` 与生成后的 `Memory` 分开保存。每条 Memory 都保留来源 Observation 和 Session，可以完整追溯。

## 快速开始：本机 SQLite

环境要求：Python 3.12 或更高版本。

```bash
git clone git@github.com:dawncc/codex-lan-memory.git
cd codex-lan-memory
python -m venv .venv
```

激活虚拟环境并安装项目：

```bash
# Linux / macOS
source .venv/bin/activate
pip install -e ".[dev]"

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

初始化 SQLite：

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

在两个终端中使用相同的 `DATABASE_URL` 分别启动：

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

可选：写入演示数据。

```powershell
.\.venv\Scripts\python scripts\seed_demo.py
.\.venv\Scripts\python scripts\seed_conversation_demo.py
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

## 使用 Docker Compose 部署

Compose 会同时启动 PostgreSQL/pgvector、API/UI 服务和 Worker。

```bash
cp .env.example .env
cd deploy
docker compose up --build -d
```

访问 `http://<服务器局域网IP>:8000`。PostgreSQL 只在 Compose 内部网络中开放。

## 连接 Codex

在每台 Codex 机器上安装本项目：

```bash
pipx install .
```

在 `$CODEX_HOME/config.toml` 中加入：

```toml
[mcp_servers.codex_memory]
command = "codex-memory-mcp"
args = ["--server", "http://192.168.1.100:8000"]
startup_timeout_sec = 30
```

将 `skill/codex-memory` 复制到 `$CODEX_HOME/skills/codex-memory`，重启 Codex，然后调用 `memory_health` 验证连接。

该 Skill 会指导 Codex：

1. 在复杂任务开始前召回相关项目历史；
2. 保存可长期复用的决策、经验、问题和解决方案；
3. 归档完整对话及所有可用的工具结果；
4. 存在精确耗时时提交 `duration_ms`；
5. 在会话结束或压缩前提交结构化总结。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `memory_submit` | 保存观察、决策、经验、问题、解决方案或会话总结。 |
| `memory_recall` | 返回结构化结果和可直接注入上下文的 `<chat-memory>` 文本块。 |
| `memory_session_end` | 提交完成事项、决策、未解决问题和相关文件。 |
| `memory_chat_submit` | 保存完整、按时间排序的 user/assistant/system/tool 聊天记录。 |
| `memory_health` | 检查服务端和数据库，并补传本地 pending 队列。 |

## HTTP 接口

MVP 只保留少量核心业务接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/observations` | 接收长期观察与 Session 总结。 |
| `POST` | `/api/v1/chat/messages` | 接收完整聊天批次与工具元数据。 |
| `POST` | `/api/v1/recall` | 执行混合 Memory 召回。 |
| `GET` | `/api/v1/health` | 检查数据库和待处理任务。 |

主要网页路由：

- `/`：系统概览
- `/search`：Memory 搜索
- `/calendar`：每日记忆日历
- `/traces`：Session 请求路径与耗时
- `/tools`：工具执行归档
- `/faq`：失败工具 FAQ
- `/sessions/<id>`：完整聊天记录

## Memory 生命周期

1. **Capture**：首先保存原始 Observation 或完整聊天。
2. **Compress**：可使用 OpenAI-compatible 模型；未配置 LLM 时使用确定性的规则压缩。
3. **Consolidate**：只合并类型兼容的 Memory，并保留所有来源链接。
4. **Retrieve**：融合精确短语、BM25、模糊匹配、元数据和可选 embedding。

成功工具生成 `tool_success` Memory。失败工具单独生成 `tool_failure_faq` Memory，避免相似的成功输出和失败输出被错误合并。状态未知的执行只归档，不提升为 Memory。

## Trace 耗时规则

如果提交端知道精确耗时，请在 `memory_chat_submit` 的消息顶层传入 `duration_ms`。Trace 页面用蓝色展示精确值。旧消息没有耗时字段时，会使用相邻消息时间差，并明确标记为“估算”。活跃耗时不会把两个独立用户请求之间的空闲时间计算进去。

## 连接远程 Codex 主机

如果远程主机无法直接访问 Memory Server 的局域网地址，可以从 Memory Server 所在机器建立 SSH 反向隧道：

```bash
ssh -N -R 18000:127.0.0.1:8000 <remote-host>
```

远程 MCP 使用 `--server http://127.0.0.1:18000`。仓库中的 `scripts/test_remote_codex.sh` 会通过真实远程 Codex 进程验证健康检查、聊天提交、成功/失败工具归档、FAQ 和 Recall。

长期部署更推荐可直接访问的局域网、VPN 或私有网络地址；SSH 隧道只会在 SSH 进程存活期间有效。

## 配置项

| 环境变量 | 默认值 / 示例 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://memory:memory@postgres/memory` | SQLAlchemy 数据库地址；本机可使用 `sqlite:///./demo.db`。 |
| `MEMORY_SERVER_HOST` | `0.0.0.0` | API 监听地址。 |
| `MEMORY_SERVER_PORT` | `8000` | API 和网页端口。 |
| `MEMORY_LLM_ENABLED` | `false` | 是否启用 OpenAI-compatible 压缩。 |
| `MEMORY_LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible 服务地址。 |
| `MEMORY_LLM_MODEL` | `qwen3` | 压缩模型名称。 |
| `MEMORY_EMBEDDING_ENABLED` | `false` | 是否启用本地 embedding。 |
| `MEMORY_EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | sentence-transformers 模型。 |
| `MEMORY_RECALL_CANDIDATE_LIMIT` | `0` | `0` 表示搜索全部 Memory；数据量大时应设置候选上限。 |
| `MEMORY_DAILY_REFRESH_SECONDS` | `60` | 每日总结刷新间隔。 |
| `MEMORY_TIMEZONE` | `Asia/Shanghai` | 每日总结使用的时区。 |

## 可选 Session End Hook

设置 `MEMORY_SERVER_URL`，确保 Hook 使用的 Python 安装了 `httpx`，然后将 `hooks/session-end.py` 注册到 Codex `Stop` 和 `PreCompact`。如果服务端暂时不可用，Hook 会写入 `$CODEX_HOME/codex-memory/pending.jsonl`，MCP 桥接进程会在后续调用时自动补传。

## 工程结构

```text
packages/memory-common/   公共协议、配置、本地 pending 队列
packages/memory-server/   FastAPI、SQLAlchemy、召回、Trace、Jinja2 页面
packages/memory-worker/   压缩、归并、embedding、每日总结
packages/memory-mcp/      本地 stdio MCP 到 HTTP 的桥接
skill/codex-memory/       Codex 工作流 Skill
hooks/                    Stop / PreCompact 兜底 Hook
migrations/               Alembic 数据库迁移
deploy/                   Docker Compose 部署
scripts/                  演示数据与远程验证脚本
tests/                    单元和集成测试
docs/images/              README 截图
```

## 开发与测试

```bash
pip install -e ".[dev]"
pytest
```

当前测试覆盖聊天完整保存、中英文召回、项目隔离、本地 pending 重试、工具状态分类、FAQ 构造、Worker 归并边界、Trace 耗时和 HTML 转义。

## 安全边界

该 MVP 假设运行在可信私有网络中，暂未实现 TLS、鉴权、设备 Token、租户隔离和自动脱敏。将服务暴露到可信局域网或 VPN 之外之前，必须补充这些控制。

## 版权与开源许可

Copyright (c) 2026 dawncc。

本项目是依据 [MIT License](LICENSE) 开放源代码的软件。你可以在遵守许可证条款的前提下使用、复制、修改、合并、发布、分发、再许可及销售本软件的副本。
