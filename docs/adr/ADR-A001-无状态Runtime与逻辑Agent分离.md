# ADR-A001 无状态 Runtime 与逻辑 Agent 分离

**决策**：`AgentDefinition` 表示逻辑 Agent；`RuntimeInstance` 表示 Pod/Process；默认由共享 RuntimePool 执行。任何 durable fact 不依赖 RuntimeInstance identity。

历史"Agent=Pod"的架构目标（不绑定逻辑生命周期）保留，但术语废弃。

**验收**：真实两个以上 Pod、无 sticky session、kill/scale/rolling restart 后持续服务且无事实丢失。
