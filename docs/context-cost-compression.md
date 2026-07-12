# 多轮任务的成本控制与上下文压缩

## 结论

推荐采用“硬预算 + 分层上下文 + 轮次相关性 + 可回放评测”，而不是只依赖模型上下文窗口变大或单次 LLM 摘要。

本仓库现有的完整聊天归档是冷层，Memory/Observation 是长期层；新增的 `memory_common.context_compression` 负责在每次模型调用前构造有预算的工作集。完整原文永不因压缩删除，压缩结果只作为可重建的派生视图。

## 业界方案比较

| 方案 | 机制 | 优点 | 多轮任务风险 | 本项目采用方式 |
| --- | --- | --- | --- | --- |
| 滑动窗口/裁剪 | 只保留最近 N 条或 N token | 便宜、确定性强 | 早期约束和决策直接丢失 | 仅用于最近工作集，不单独使用 |
| 滚动摘要 | 用摘要替换旧消息 | 可读、压缩率高 | 摘要漂移，反复摘要累积损失 | 作为派生层，保留来源 sequence |
| 检索式记忆 | 从长期存储召回相关片段 | 跨会话、按需加载 | 查询措辞不一致导致漏召回 | 复用当前 BM25/embedding/反馈排序 |
| 分层/虚拟上下文 | 热工作集、摘要、归档分层换入换出 | 适合长任务，可恢复 | 状态机和评价更复杂 | 作为总体架构 |
| token/句子压缩 | 删除低信息 token 或句子 | 压缩率高 | 代码、数字、否定词和格式可能损坏 | 后续仅压缩自然语言检索材料 |
| Provider 原生 compaction | 服务端生成可继续使用的压缩项 | 接入简单 | Provider 绑定、可解释性弱 | 可选第二级降级，不作唯一存档 |

调研依据：OpenAI Responses API 定义了 `v1/responses/compact` 生成的 compaction item；LangGraph 的短期记忆文档列出 trim、delete、summarize 和自定义过滤；MemGPT 用操作系统虚拟内存类比分层管理上下文；LLMLingua 系列使用预算控制和 token/句子选择；“Lost in the Middle”说明“能放进窗口”并不等于模型能可靠使用中间信息。

## 建议架构

每次请求的输入预算：

```text
usable = (model_context - reserved_output) * (1 - safety_margin)
usable = pinned + current_task + recent_turns + retrieved_memory + old_turn_summary + tool_evidence
```

优先级从高到低：

1. 不可压缩：系统/仓库约束、当前用户任务、明确标记 `protected` 的内容。
2. 结构化保真：错误行、退出码、文件路径、代码 diff、JSON schema、数字和否定约束。
3. 最近工作集：最近 2–6 个完整用户轮次，按 token 而非消息数控制。
4. 相关旧轮次：以用户发问开始的整轮为单元评分；相关性从问题传播到回答和工具证据。
5. 长期记忆：从 Memory 检索决策、约束、未解决问题，按置信度和反馈重排。
6. 可损压缩：只对自然语言说明和普通日志做抽取或 LLM 摘要。

压缩采用高低水位，避免每轮重写摘要：达到可用预算的 85% 时压到 60–70%。关键事实门禁不通过时扩大预算或回退原文。摘要记录来源 sequence 和策略版本，支持审计与重新生成。

## 已实现基线

[`memory_common.context_compression`](../packages/memory-common/src/memory_common/context_compression.py) 提供：

- 无 tokenizer 依赖的保守 token 估算；生产可注入模型 tokenizer。
- 输入/输出预留、安全余量、最近轮次、摘要和工具输出独立预算。
- 系统/当前/protected 消息硬保护。
- 用户主导的轮次级相关性传播，避免问题与答案拆散。
- 工具输出保留首尾、错误/异常行，压缩普通日志。
- `CompressionReport` 输出 token、丢弃/摘要/保护消息数和压缩率。

模块暂不直接改写数据库或 MCP 行为，便于先以影子模式收集数据。接入点应在实际模型请求组装之前，而不是聊天归档入口。

## 实验

运行：

```powershell
$env:PYTHONPATH='packages/memory-common/src'
.\.venv\Scripts\python.exe scripts/experiment_context_compression.py
```

合成回放含 14 条消息、4 个多轮子任务、大段工具日志、系统安全约束和早期数据库端口决策。关键事实为 `55432`、安全约束和查询主题。

| 策略 | 输入估算 token | 压缩率 | 关键事实召回 | 预算内 |
| --- | ---: | ---: | ---: | --- |
| 完整历史 | 9,498 | 0% | 100% | 基线 |
| 分层压缩，6,000 预算 | 1,953 | 79.44% | 100% | 是 |
| 分层压缩，3,000 预算 | 1,953 | 79.44% | 100% | 是 |
| 分层压缩，1,500 预算 | 1,059 | 88.85% | 100% | 是 |

这是确定性合成实验，只证明预算、保护和关键字恢复机制按设计工作，不证明真实模型任务质量。第一版曾因按单消息评分，把“数据库”问题与只写“PostgreSQL”的答案拆开，紧预算下关键事实召回降到 66.7%；改为轮次级相关性传播后恢复到 100%。

## 生产实验计划

### Phase 1：影子评估

- 从归档 Session 分层采样短/中/长、多工具/少工具、成功/失败任务各至少 30 个。
- 每轮生成 full、tail-only、hierarchical 三份上下文，不改变线上请求。
- 记录 token、耗时、事实/约束召回、代码与 JSON 可解析性。

### Phase 2：模型回放

- 使用相同模型、temperature 和工具快照回放。
- 质量指标：任务成功率、测试通过率、工具调用次数、关键决策一致率。
- 成本指标：输入/缓存输入/输出 token、摘要成本、端到端延迟。
- 门禁：系统约束和当前任务 100%，错误证据与数字事实至少 99%。

### Phase 3：小流量上线

- 先压工具输出，再启用旧轮次摘要，最后才启用 token 级自然语言压缩。
- 预算超限、门禁失败、摘要膨胀或解析失败时回退 recent + 原始证据。
- 按任务类型调预算，不用单一全局压缩率。

## 成本模型与默认值

```text
cost = uncached_input_tokens * input_price
     + cached_input_tokens * cached_input_price
     + output_tokens * output_price
     + compression_cost
```

推荐初值：输出预留为 context 的 15%–25%、`safety_margin=10%`、`recent_turns=4`、高水位 85%、目标水位 65%。代码/测试/修复任务提高原始证据预算；闲聊与说明任务可提高摘要比例。只有预计未来复用节省大于摘要调用成本时才调用 LLM 摘要。

## 后续顺序

1. 注入真实 tokenizer 和 Provider 价格配置，不写死动态价格。
2. 新增压缩快照与来源映射，做增量摘要，不覆盖完整聊天。
3. 在模型调用网关增加 shadow/active/off 和指标埋点。
4. 用真实归档 Session 建评测集，再决定是否引入 LLM 摘要或 LLMLingua。
5. 对 Provider 原生 compaction 做 A/B，保留本地策略作为跨 Provider 基线。

## 资料

- [OpenAI API compaction item](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal/delta)
- [LangGraph manage short-term memory](https://langchain-ai.github.io/langgraph/how-tos/memory/manage-conversation-history/)
- [MemGPT](https://arxiv.org/abs/2310.08560)
- [LLMLingua](https://arxiv.org/abs/2310.05736)
- [LongLLMLingua](https://arxiv.org/abs/2310.06839)
- [Lost in the Middle](https://arxiv.org/abs/2307.03172)
