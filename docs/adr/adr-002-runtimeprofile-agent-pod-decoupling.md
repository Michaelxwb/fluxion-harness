# ADR-002: RuntimeProfile 与 Agent Pod 解耦

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P05
- **V4.1 对应**: AgentDefinition 与 Runtime Pod 解耦

## Context

旧项目 `Agent A → Deployment A`，Prompt/Skill/Model 修改与 Deployment 生命周期纠缠，大量低频 Agent 造成资源浪费。`Logical Agent Lifecycle = Infrastructure Lifecycle`。

## Constraints

- Console 管理运行态配置，不直接管理 Pod。
- 默认共享无状态 Runtime Pool；高隔离/GPU/Sandbox 场景才走 Dedicated Runtime。

## Decision

- **术语**：Console 创建/发布的对象是 **RuntimeProfile**；运行中的 **Agent** 是实际 Runtime Service/Pod（与 V4.1 的 AgentDefinition / Agent Runtime 对应，语义一致、命名重排）。
- Console 创建 RuntimeProfile 不产生任何 K8s Pod 动作。
- 多个 RuntimeProfile 默认共享 Runtime Pool；`runtime_policy: dedicated|sandbox|gpu|remote` 是调度策略而非默认部署方式。
- 所有 Agent Pod 从 Registry 读取同一套 RuntimeProfile/Binding/Policy，等价解析。

## Trade-offs

- 换取逻辑与算力解耦、资源利用率，代价是 Runtime 依赖 Registry 作为事实源（见 ADR-004）。
- Dedicated Runtime Controller 可延后实现（V1 只定义策略 Contract）。

## Failure Modes

- RuntimeProfile 误以为等价 Pod → 违反 P05。用 S-C101 断言"创建 RuntimeProfile 且无 K8s Pod 动作"防回归。

## Validation

- S-C101：Console 创建 RuntimeProfile 无 Pod 动作。
- S-C104：同用户 Binding 可被多 Registry 实例等价解析。
- S-R05 / B-R03：共享 Pool 下多 Pod 一致性。

## Revisit Conditions

- 出现必须与特定算力一一绑定的产品场景且无法用 runtime_policy 表达。
