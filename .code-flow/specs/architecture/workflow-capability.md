---
id: fluxion-workflow-capability
description: Fluxion Tool、Capability、Workflow、A2A 的职责边界
stages: [design, plan, code, review]
enforcement: required
verifiers:
  - rule: RULE-fluxion-workflow-001
    type: manual
    config:
      checklist: 检查 Tool/Workflow 是否复用 Capability Contract，Agent Runtime 是否没有持久 Workflow 状态。
      owner: project-owner
---

# Workflow 与 Capability 规范

## Rules

- [RULE-fluxion-workflow-001] Tool 只是 Agent-facing Adapter；业务逻辑属于 Capability；复杂 SOP 的 Durable State 必须由 Workflow Engine 管理。

## Guidance

- Agent 负责 Intent、Reasoning、Decision。
- Workflow Engine 负责 State、Retry、Compensation、Approval、Timeout、Recovery、Idempotency。
- Workflow 可以作为 Agent 的粗粒度 Tool。
- Workflow Step 和 Agent Tool 不允许分别复制业务实现，应共同调用 Capability。
- A2A V1 只实现满足现阶段协作的最小 Contract，不提前实现复杂标准全集。
