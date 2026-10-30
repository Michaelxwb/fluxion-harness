# ADR：执行核心去框架化

- **日期**：2026-10-31
- **状态**：已采纳
- **关联需求**：`.code-flow/tasks/2026-10-31/execution-core-deframework/`
- **影响范围**：`packages/agent-core`、`migrations/`、依赖清单、specs 与交付文档

## 背景

产品基线曾指定 agent-runtime 使用 Python + LangGraph，并以「保留 interrupt/checkpoint 能力」为理由选择 LangGraph + PG Checkpointer。实现中：

- 全仓只有 `agent/runner.py` 使用 `StateGraph` 构建 5 节点循环，`compile()` 从未挂 checkpointer，也没有任何 `langgraph.checkpoint` 引用；
- 中断由 `run_interrupt` 业务表与 `ToolSuspensionPort` 自研实现，恢复是从 canonical events 重建后重新执行，不依赖框架状态；
- 执行真相是多 Pod 租约 + epoch fence + canonical events，与「checkpoint 即真相」的持久执行模型互斥。

保留该依赖只带来：约 10 个包进入全部服务镜像、每次 Run 一次图编译开销、以及归档文档中「checkpointer 未接线，列入遗留缺口」这一会引导错误改造的入口。

## 决策

1. 执行核心改为显式阶段循环：删除 `StateGraph`、`_build_graph`、`ainvoke`，保留全部阶段方法、路由函数、预算/取消/挂起/事件注入/流式回调语义。
2. 从依赖清单移除 langgraph 与 langchain 栈，重生成锁与共享 requirements。
3. 新增迁移 `0029` 删除从未使用的 `langgraph` schema，downgrade 重建空 schema。
4. 当前 specs 与交付文档按代码事实改写；历史记录保留。

## 被否方案

- **接 PG Checkpointer（被否）**：会把框架状态变成第二真相；checkpointer 没有租约/epoch fence，两个 Pod 可同时写同一 thread；tools 节点恢复会重放已执行的工具副作用；状态含回调与端口对象，不可序列化。
- **保留 StateGraph 造型（被否）**：只保留排版价值，继续携带依赖与错误改造入口。
- **拆分 runner 为三个对象（被否）**：42 个方法中位数 12 行、最长 45 行，已满足单函数上限；拆分需要在对象间共享状态与回调，增加间接层。

## 后果

- 正面：依赖面减少约 10 个包；每 Run 的图编译开销归零；当前文档与 specs 与代码事实一致。
- 代价：无行为变化；`langgraph` schema 直接删除（无数据），回滚依赖代码 revert 与迁移 downgrade。
- 历史记录：`docs/17-V1.4评审修订说明.md`、`scripts/capacity/ANALYSIS.md` 的 1.8% 实测、归档任务文档保留原文，仅作历史证据；本 ADR 取代「checkpointer 遗留缺口」的待办含义。
