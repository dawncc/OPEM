# OPEM 自演进调研与实施计划

## 初步结论

自演进不应等同于 Agent 自动重写自身。适合 OPEM 的可控闭环是：采集任务结果 → 形成可审计标签 → 离线回放 → 搜索低风险策略参数 → 安全门禁 → shadow/canary → 可回滚发布。当前阶段只演进检索排序参数，不自动删除 Memory、修改提示词或修改代码。

## 常用方法与适用范围

| 方法 | 机制 | OPEM 采用方式 |
| --- | --- | --- |
| Reflexion | 把环境反馈转成可复用的语言反思 | 将 verified outcome 记录为 helpful/irrelevant/harmful，保留 query 和原因 |
| Self-Refine | 生成、反馈、迭代改进 | 用于后续生成候选 Memory，不直接覆盖已验证 Memory |
| Voyager | 环境验证、迭代修复、技能库复用 | 成功经验必须保留验证证据和适用范围 |
| DSPy/GEPA 类优化器 | 在固定 train/dev metric 上搜索 prompt/参数 | 当前先对融合、置信度、多样性参数做可审计小网格搜索 |
| 多臂老虎机/贝叶斯优化 | 在线探索候选策略 | 数据量足够后再用于 canary；必须限制探索流量和安全损失 |

参考：[Reflexion](https://arxiv.org/abs/2303.11366)、[Self-Refine](https://arxiv.org/abs/2303.17651)、[Voyager](https://arxiv.org/abs/2305.16291)、[DSPy](https://dspy.ai/)。

## 数据集与评估层次

优先级按与 OPEM 的贴合度排序：

1. **项目内反馈回放集**：由真实 query 与显式反馈构建，最能衡量领域内收益。按时间切分 train/dev/test，按 session 去重，避免同一次反馈泄漏到多个集合。
2. **LongMemEval / LongMemEval-V2**：前者覆盖信息抽取、多会话推理、知识更新、时间推理和拒答；V2 进一步覆盖轨迹经验、工作流知识与大规模 haystack。适合作为长期记忆端到端基准。
3. **MemoryAgentBench**：覆盖准确检索、test-time learning、长程理解和冲突/遗忘能力，适合检查“会记但不会更新”的问题。
4. **LoCoMo / LoCoMo-Plus**：适合长期对话事实、时间、因果以及隐式约束一致性评估。
5. **任务型环境**：ALFWorld、WebShop、SWE-bench 等用任务成功率、工具失败率和人工纠正次数衡量 Memory 是否真正改善 Agent，而不只是检索命中。

参考：[LongMemEval 官方仓库](https://github.com/xiaowu0162/LongMemEval)、[LongMemEval-V2 官方仓库](https://github.com/xiaowu0162/LongMemEval-V2)、[MemoryAgentBench 论文](https://arxiv.org/abs/2507.05257)。

## 指标和发布门禁

- 检索：Recall@K、MRR、nDCG@K、知识更新正确率、拒答准确率。
- 安全：harmful recall rate、过时 Memory 命中率、跨项目泄漏率。
- 系统：p50/p95 latency、索引/LLM 成本、返回上下文 token 数。
- 任务：端到端成功率、工具失败率、人工纠正次数。
- 统计：bootstrap 置信区间；候选至少不劣于 baseline，安全指标不得退化。

本次实现使用 Recall@K、MRR、harmful recall rate 和 irrelevant recall rate。默认至少 20 个含 helpful 标签的 query 才允许晋级；数据不足时只输出报告。

## 已实现计划

- [x] 将排序中的低风险参数抽象为 `RecallPolicy`。
- [x] 从显式反馈按 project + query 构建确定性回放集。
- [x] 离线比较 27 组融合、置信度和多样性参数。
- [x] 加入样本量、效果不退化、安全不退化的 promotion gate。
- [x] 增加命令行 JSON 报告和单元测试。
- [ ] 引入时间切分和 bootstrap 置信区间。
- [ ] 接入 LongMemEval-S 的 retrieval-only adapter，再做端到端 QA judge。
- [ ] 增加 shadow 日志、策略版本、canary 和自动回滚。

## 使用方式

```powershell
$env:PYTHONPATH='packages/memory-common/src;packages/memory-server/src'
python scripts/evaluate_self_evolution.py --project OPEM --k 5 --min-cases 20
```

输出包含 baseline、最佳候选、各项指标及 `promotion_allowed`。候选策略不会自动应用到线上。
