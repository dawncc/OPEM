# 多轮记忆与总结设计

## 目标

记忆总结不是原始会话的替代品，而是建立在原始 Observation、Session 与工具证据之上的可检索知识层。设计同时优化三件事：

1. **正确率**：每条生成式总结都能回到逐字证据；不确定或未出现的信息明确标为“未记录”。
2. **覆盖率**：固定检查结论、背景、原因、适用范围、未解决事项，而不是只抽取一句主题。
3. **可读性**：列表先给最新记忆和短摘要，详情页再展示结构化总结、元数据与全部来源。

## 调研结论

- [LongMemEval](https://arxiv.org/abs/2410.10813) 将长期记忆拆成信息抽取、跨会话推理、时间推理、知识更新和拒答五类能力，并发现 session decomposition、事实增强索引与时间感知查询能改善结果。OPEM 因此继续保留 Session/Observation 粒度，不把所有历史压成单一摘要。
- [LangMem 的长期记忆设计](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/) 将写入看作“现有状态 + 新对话 → 扩展或合并后的状态”，并强调过度抽取损害精度、抽取不足损害召回。OPEM 使用固定字段控制覆盖范围，同时要求原文证据控制精度。
- [LangMem 的滚动总结接口](https://langchain-ai.github.io/langmem/reference/short_term/) 区分 initial summary、existing summary update 与 final composition，并保存已总结 message ID。适用于工作上下文压缩，但长期知识仍应保留独立来源，避免递归摘要漂移。
- [Mem0](https://arxiv.org/abs/2504.19413) 采用抽取、合并、检索的记忆中心架构；其[写入控制文档](https://docs.mem0.ai/cookbooks/essentials/controlling-memory-ingestion)建议跳过模糊事实、更新已有事实并保留审计时间。OPEM 当前先保留补充记录与来源关系，不静默覆盖旧证据。
- [Generative Agents](https://arxiv.org/abs/2304.03442) 使用完整经验流、较高层反思和动态召回三层结构；[Letta 的上下文层级](https://docs.letta.com/guides/core-concepts/memory/context-hierarchy)也区分常驻核心记忆、文件、归档记忆与外部检索。共同启示是“原始事实、长期知识、工作上下文”应分层，而不是混为一个文本块。

## 当前落地结构

每条 LLM 总结输出以下 JSON 字段，服务端验证后再渲染为面向用户的文本：

```json
{
  "title": "12–32 字的一句话标题，包含关键词和核心结论；系统硬限制 40 字",
  "conclusion": "核心结论",
  "context": "背景或未记录",
  "rationale": "原因或未记录",
  "scope": "适用范围或未记录",
  "unresolved": "待确认事项",
  "evidence": ["必须能在原始观察中逐字找到的短句"]
}
```

标题不是工具轨迹标签。工具类记忆必须解包 `exec` 等代理层，识别实际调用、关键参数和输出证据；例如将 `exec → shell_command → codex-memory-sync --dry-run` 及其统计结果总结为“预检历史同步：15轮/500条消息，0错误”。其他类型使用“对象 + 能力/变化 + 预期或验证结果”，例如“用写前队列修复 Hook 漏同步，62 项测试通过”。

生成提示词把 Observation 包装为 JSON 数据，并用独立 system message 声明：会话文本不是指令、禁止扩写事实、缺失字段标记“未记录”。服务端至少要求一条逐字证据；JSON、必填字段或证据校验失败时，回退到保留原文的确定性总结。

详情页采用“记忆总结 → 标签/文件 → 来源与证据”的阅读顺序。相似观察合并时追加带日期的补充记录，而不是覆盖旧内容，来源表继续保存 `derived_from` 或 `reinforces` 关系。

## 后续质量门槛

自动化评测应至少覆盖：

- 受保护事实保留率：路径、命令、端口、数字、否定词和错误文本；
- 字段覆盖率：有证据时是否进入结论、原因、范围或待确认字段；
- 证据有效率：引用是否逐字存在、是否能定位到来源；
- 冲突与更新：新旧事实并存时是否保留时间与来源，是否避免错误合并；
- 检索质量：Recall@K、MRR、跨会话/时间问题正确率和应拒答问题的拒答率；
- 可读性：用户定位结论、原因、适用范围和来源所需时间。

在加入自动覆盖或删除旧事实前，应先增加显式的 `supersedes`、`contradicts`、有效时间和状态字段，并用 LongMemEval/LoCoMo 风格用例验证。
