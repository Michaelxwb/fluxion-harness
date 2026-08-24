# ADR-007: Typed Lifecycle Hook

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P09

## Context

鉴权、DLP、安全检查、Tool Permission、审批、Prompt Enhancement、Semantic Validation、Audit、Trace、Metrics、Eval 都需要插入 Agent 生命周期。如果全部硬编码进 Executor，Executor 会再次膨胀。

## Constraints

- Hook 通过 Plugin + Typed Event 实现，不维护彼此割裂的 PluginManager/HookManager/EventManager。
- Hook Contract 必须支持 `priority`、`timeout`、`fail_policy`、`scope`。
- 首版至少覆盖 request/context/agent_run/llm/tool/skill/mcp/response/retry/error 等生命周期点。

## Options

1. 横切逻辑全部硬编码进 Executor。
2. 独立 Event Bus 组件。
3. Typed Lifecycle Hook 挂在 Plugin 上。

## Decision

**Typed Lifecycle Hook。** 生命周期拦截点（before_request、after_context_resolved、before/after_agent_run、before/after_llm_call、before/after_tool_call、before_response、on_retry、on_error 等）作为 Typed Event 暴露，Hook 是挂在这些事件上的 Plugin。每个 Hook 必须声明 `priority / timeout / fail_policy(fail_open|fail_closed|ignore) / scope(global|tenant|agent|user)`。

## Trade-offs

- 换取 Executor 不膨胀与横切能力可组合，代价是 Event 类型需要随生命周期演进、Hook 顺序与预算需要治理。

## Failure Modes

- Hook 长尾拖慢主链路 → 用 `timeout` + latency budget + NFR-PERF（Hook P95≤10ms）约束。
- Hook fail 策略不一致导致安全洞 → fail_closed 用于安全类 Hook，策略显式声明。

## Validation

- S-R06 / E-R06：Hook 顺序与 fail_policy 生效。
- Hook P95≤10ms benchmark。

## Revisit Conditions

- 生命周期点稳定后出现新的横切维度无法映射到现有 Event。
