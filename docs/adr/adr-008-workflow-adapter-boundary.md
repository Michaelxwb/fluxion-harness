# ADR-008: Workflow Tool Adapter 接入协议在开源 V1；Workflow Engine/业务归业务层

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P11, P21
- **Amended by**: ADR-013（Durable Workflow Engine vendor 已定 = DBOS，2026-08-28）

## Context

复杂 SOP（员工入职等）包含 Retry、Compensation、Timeout、Human Approval、Long-running State、Crash Recovery、Idempotency，属于 Durable Workflow，不是 LLM Reasoning。Fluxion 定位为业务无关的开源 Agent Harness（见 Architecture Baseline §12）。

开源 V1 需要让 Agent 能调用 Workflow（作为粗粒度 Tool），但**不开发 Workflow Engine/DSL/业务定义**。因此边界必须区分：**接入协议（开源）** 与 **Engine/业务（业务接入层）**。

## Constraints

- Agent 负责 Intent/Reasoning/Decision；Workflow Engine 负责 State/Retry/Compensation/Approval/Recovery。
- Agent 通过 `execute_workflow(...)` 调用，获得 `workflow_run_id`，Runtime 不保存 durable state。
- 开源 V1 范围 = Agent + Console（业务无关）；业务接入时才构建对应 Workflow。

## Options

1. Workflow Engine 内置进开源 V1。
2. Adapter 接入协议也不实现，Workflow 完全交给业务层。
3. **Adapter 接入协议在开源 V1 实现；Engine/DSL/业务归业务接入层。**

## Decision

**Option 3。** 两个边界分开：

```text
开源 V1（实现）
├── Workflow Tool Adapter（FEAT-13）
│   ├── execute_workflow 请求/响应契约
│   ├── workflow_run_id 返回语义
│   ├── error / async 语义
│   └── 以 Workflow Engine Stub 验证（S-R08）

业务接入层（不开发，接入时构建）
├── Workflow Engine / DSL / Durable State
├── 业务 WorkflowDefinition 与 SOP
└── 真实 Engine（现以 DBOS 构建，见 ADR-013）替换 Adapter 的 Stub
```

Runtime 不持有 Workflow durable state。Console 的 WorkflowDefinition 管理（FEAT-09）降为 P2 占位，属业务接入层。

## Trade-offs

- 换取开源项目业务无关、Agent 侧接入能力完整，代价是业务方需自行构建 Workflow Engine（现以 DBOS 构建，见 ADR-013；Retry/Compensation/Approval/Recovery）。
- 与 V4.1 §12.5 时序一致（Workflow 详细设计在 Runtime/Console 之后），边界调整为"Adapter 开源、Engine 业务层"。

## Failure Modes

- Adapter 契约与未来真实 Engine 漂移 → Stub 契约先固定，业务接入时以真实 Engine 校验并收敛。
- Workflow 业务需求外溢回开源 Core → 用 §12 分层规则禁止，业务逻辑只走 Adapter。

## Validation

- S-R08（TASK-004）：Agent → Workflow Adapter → Workflow Engine Stub，返回 `workflow_run_id`，Runtime 无 durable workflow state。
- 业务接入阶段：以真实 Engine 替换 Stub 后契约不漂移。

## Revisit Conditions

- 出现跨企业通用的确定性流程，且其 Engine 收益大于维护成本——此时重新评估是否纳入开源层。
