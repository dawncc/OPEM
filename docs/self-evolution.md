# OPEM 自演进设计

## 目标与边界

OPEM 的“自演进”是一个可观测、可评估、可回滚的记忆质量闭环，而不是让 Agent 无审批地修改自身代码或模型权重。

闭环为：采集经验 → 压缩和归并 → 召回 → 执行任务 → 回传效果 → 校准置信度 → 改善后续排序。所有反馈都保留原始 query、原因、session 和时间，可审计；删除 Memory、修改提示词、生成代码等高风险动作不在自动闭环内。

## 业界方案调研

| 方案 | 核心机制 | 对 OPEM 的启发 |
| --- | --- | --- |
| [Reflexion](https://arxiv.org/abs/2303.11366) | 将标量或文本反馈转为反思，写入 episodic memory，不依赖权重微调 | 先演进外部记忆；反馈必须来自任务结果 |
| [Voyager](https://arxiv.org/abs/2305.16291) | 环境反馈、自验证、迭代改进和可复用技能库 | 成功经验需可组合、可检索，并保留验证证据 |
| [MemGPT](https://arxiv.org/abs/2310.08560) | 分层记忆和受控的数据迁移，扩展有限上下文 | 原始会话、长期 Memory 和工作上下文应分层管理 |
| [DSPy Optimizers](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/learn/optimization/optimizers.md) | 围绕明确 metric 和样本优化 prompt 或模型参数 | 没有评估指标就不应自动优化；后续优化必须先建离线评测集 |

共同模式不是让模型自由重写自己，而是 **反馈、度量、验证、复用**。因此本项目优先演进检索策略与 Memory 置信度。

## 已实现：反馈驱动的置信度校准

`POST /api/v1/memories/feedback` 和 MCP `memory_feedback` 接受三类结果：

- `helpful`：Memory 对已验证结果有帮助；
- `irrelevant`：被召回但不适用于当前任务；
- `harmful`：内容陈旧或把任务引向错误结果。

反馈写入独立的 `memory_feedback` 表，支持幂等键。置信度使用带先验的校准：初始先验为 0.7，helpful 增加正证据，irrelevant 增加负证据，harmful 计双倍负证据，结果限制在 `[0.05, 0.98]`。现有召回排序使用 `Memory.confidence`，因此后续查询会自动受反馈影响。

这一设计刻意不自动删除低分 Memory：负反馈可能只说明特定 query 下不适用。原始反馈可以支持未来按 query、时间或调用方细分模型，也能回放并重新计算。

## 演进路线

### Phase 1：在线证据闭环（已实现）

- 可审计、幂等的 recall feedback；
- 基于正负证据的置信度校准；
- 单元测试覆盖奖励、惩罚和重复提交。

### Phase 2：离线评估与影子发布

- 从历史 query 和反馈生成固定评测集；
- 指标至少包含 Recall@K、MRR、有害召回率和用户纠正率；
- 新排序器只做 shadow evaluation，不直接替换线上版本；
- 达到预设阈值并通过回归测试后由人工批准发布。

### Phase 3：反思与候选策略生成

- 将重复失败聚合为反思候选，而非直接写入高置信 Memory；
- 候选 Memory 必须包含来源、适用范围和反例；
- 新 prompt、合并阈值或排序权重由优化器产生，但通过离线评测门禁。

### Phase 4：受控自治

- 使用版本化策略、canary、自动回滚和变更审计；
- 只有低风险参数允许自动发布；数据删除、代码修改、权限变化继续要求人工审批；
- 防止单个 session 重复投票、恶意反馈和 reward hacking。

## 验收指标

短期验收：反馈写入成功率、幂等冲突率、各 outcome 分布、置信度变化可解释。中期验收：在固定评测集上 MRR/Recall@K 提升且 harmful recall rate 不上升。长期验收：相同任务的人工纠正次数和失败工具调用率持续下降，同时保留一键回滚能力。
