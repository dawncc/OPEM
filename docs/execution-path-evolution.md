# 零样本执行路径自演进

## 边界

OPEM 不把工具成功、文本相似度或任务正常结束当作任务成功，也不把相关性当作路径必要性。系统从真实使用中积累 `TaskRun`、执行节点、结果证据和策略分配；在没有消融证据时，`PathScore.necessity` 与 `PathScore.importance` 保持为空。

OPEM 当前负责观察、评价和提供版本化建议。只有宿主执行器明确支持并遵循 `active` 或 `canary` 建议时，模型级别、Agent 数量或工具预算才会改变。`observe` 与 `shadow` 永远不改变执行。

## 数据流

```text
ChatMessage / ToolExecution
  -> TaskRun + ExecutionNode DAG
  -> EvaluationJob
  -> OutcomeEvidence + PathScore
  -> offline policy report
  -> shadow candidate
  -> bounded low-risk canary (disabled by default)
```

每个用户主导的轮次对应一个 `TaskRun`。TaskRun 按 `session_id + turn_id` 幂等，保存任务类型、风险、模型、reasoning effort、策略臂、成本和采集完整度。工具、验证器和 Agent 分支保存为 `ExecutionNode`。

## 证据等级

- `strong`：确定性测试、CI、显式用户结果或真实环境结果。
- `medium`：明确接受、阻塞报告或其他较可靠但非确定性证据。
- `weak`：用户纠正候选、语义关系或 Judge 结果。
- 无证据：保持 `unknown`，不生成隐式成功标签。

同一任务、同一 `independence_group` 只使用权重最高的一条证据，避免一次任务中的大量测试或重复反馈被当作大量独立样本。工具失败默认只评价 `tool_reliability`，不会自动判定 `task_success`。

## 路径分数

观察型评分保存以下独立分量：

- 最终结果相关性；
- 下游依赖；
- 新颖性；
- 验证价值；
- 冗余度；
- 直接金额成本和关键路径耗时。

只有 `POST /api/v1/paths/interventions` 或 MCP `memory_path_intervention` 提交真实 `paired_replay` / `randomized` 结果后，系统才填写必要性和重要性。质量差值为：

```text
delta = full_quality - counterfactual_quality
necessity = max(0, delta)
importance = delta
```

## 策略门禁

默认配置：

- 强证据任务少于 20：只观察；
- 达到 20：允许生成 Shadow 候选；
- 达到 50 且人工将策略置为 Canary：可以参与 5% 低风险流量；
- Canary 总开关默认关闭；
- 高风险任务固定回退到 baseline；
- 任何策略都不会自动修改代码、删除 Memory 或扩大用户授权。

策略臂配置包含 `model_level`、`reasoning_effort`、`max_agents`、`max_tool_calls` 和 `verification_mode`。离线报告按任务类型与风险分桶，展示质量置信区间、严重失败率、成本和延迟。

## 部署与回填

应用迁移：

```powershell
$env:DATABASE_URL='sqlite:///./demo.db'
python -m alembic upgrade head
```

从已归档会话回填任务图：

```powershell
python scripts/backfill_task_runs.py --dry-run
python scripts/backfill_task_runs.py
python scripts/recompute_path_scores.py
```

生成离线报告：

```powershell
python scripts/evaluate_execution_policies.py
python scripts/evaluate_execution_policies.py --project OPEM --persist
python scripts/evaluate_execution_policies.py --project OPEM --propose-shadow
```

`--propose-shadow` 仅创建版本化 Shadow 配置，不发布、不启用 Canary。管理页面位于 `/evolution`。

## 价格配置

`MEMORY_MODEL_PRICES_JSON` 使用“模型名 -> 输入、缓存输入、输出百万 token 单价”的 JSON：

```json
{"local-model": [0.2, 0.02, 1.0]}
```

原始 usage、延迟和金额估算分别保存；未知模型明确标记为估算，不能用于声称精确成本。
