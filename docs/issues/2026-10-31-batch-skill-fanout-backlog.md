# 批量目标与逐项 skill 执行的独立需求记录

> 日期：2026-10-31
> 状态：待排期（不属于 `agent-self-evolution` 需求）
> 关联：`.code-flow/tasks/2026-10-31/agent-self-evolution/`（该需求只处理批量 case 的学习判定，不改变运行时执行方式）

## 1. 现象与问题

用户一条消息给出多个目标（例如 10 个客户），希望 agent 对每个目标分别执行同一个 skill。当前行为：

- 运行时不做扇出（fan-out）：是否逐条调用 skill 完全由模型在单次响应里发出多少个 `tool call` 决定，没有任何机制保证 10 个目标都被处理。
- 即使模型发出 10 个 `execute_skill`，它们也是**串行**执行的：`execute_skill` / `run_skill_script` 声明 `ToolEffect.EXTERNAL` 且未声明 `PARALLEL_READ`，planner 把外部工具当作串行屏障（`packages/agent-core/src/muad_agent_core/tools/planner.py` 顶部规则、`apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py` 中两个工具的定义）。
- 单次 skill 执行超时取整个 Run 的 deadline（`skill_tools.py` 构造 `SkillExecutionRequest` 时 `timeout_sec=policy.deadline_ms / 1000.0`），默认取平台设置 `agent.deadline_ms`（当前默认 1 小时）；10 次串行的总耗时仍可能超过 Run 的 deadline。
- 默认预算 `max_tool_calls=30`、`max_turns=20`、`deadline_ms=3600000`（`packages/contracts/src/muad_contracts/platform_settings.py`）。超预算的调用不会执行，模型收到 `tool call budget exhausted`（`runner.py` 的 `_reserve_tool_calls`）。
- 没有逐项账本：Run 只有终态与最终回答，无法自动判定"10 个里哪几个成功/失败"。

结论：当前是"模型自觉 + 串行 + 预算内"，不是"系统保证逐项执行"。

## 2. 可选实现路径

| 方案 | 改动面 | 预计工作量 | 风险 | 说明 |
|---|---|---|---|---|
| A. skill 接受批量输入 | 仅 skill：`input` 变为 `{items: [...]}`，脚本内循环/限并发，返回逐项结果 | 1–3 天 | 低 | 不需要改运行时；受单次执行 timeout 与 64KB 输出上限约束（`packages/agent-core/src/muad_agent_core/skill/executor.py`），逐项明细需摘要或外置 |
| B. 新增 `execute_skill_batch` 工具 | runtime / agent-core：新 `ToolDefinition` + 有界并发 + 逐项结果 | 3–5 天 + 验收/容量 | 中 | 平台级逐项账本；需同时解决执行次数预算、deadline 取消、结果体积、异步 JOIN 的多任务等待、指标与审计 |
| C. 放开外部工具并发 | planner / registry：允许 `EXTERNAL` + 资源键并发 | 代码 1–2 天 | 高 | 打破"外部副作用串行"的安全假设；顺序、幂等、审计都要重新论证，不建议作为通用改动 |

方案 B 中需要单独确认的一点：ASYNC/JOIN skill 目前一次提交对应一次 `BACKGROUND_RESULT` 恢复；一批 JOIN 任务在同一轮等待的语义需要先核实（`apps/agent-runtime/src/muad_agent_runtime/infrastructure/models/async_tools.py` 的 `ToolOperation` 与 runtime 的恢复链），这可能是 B 的主要不确定项。

## 3. 推荐

1. 先做方案 A：用 skill 自身承接批量输入，定义 `items[]`、逐项结果、单项超时、总项数上限。成本最低，且不需要改动运行时的安全边界。
2. 如果后续需要平台强制逐项账本、统一并发与预算，再做方案 B，并作为独立需求单独立项。
3. 不建议方案 C，也不建议运行时从自由文本自动解析"10 个客户"再扇出：切分歧义（"A 和 B 对比"）、误切漏项、不可回溯。

## 4. 未来需求的验收草案

- 用户一次给出 N 个目标，系统产生 N 次可归因的执行（或一条批量执行的 N 条逐项结果），逐项状态可查询。
- 任一项失败或缺失时，最终回答与状态明确标注，不把整体完成当作全部成功。
- 并发上限、单次超时、总项数上限可配置；超限显式失败而不是静默截断。
- 指标按项统计（成功/失败/超时），审计含批次与逐项 id。
- 容量验证覆盖 N=10 与更高档位，不突破现有 Run deadline 与资源预算。

## 5. 与 `agent-self-evolution` 的边界

自进化需求只做"批量 case 的学习侧正确性"：Golden Set 记录 `item_count` 与逐项判据、回放按逐项结果判定、批量轨迹生成带批量触发语义的 PROCEDURE（场景 S-14 / S-15 / S-16）。运行时的逐项执行能力不在该需求内，等本记录排期后另行立项。
