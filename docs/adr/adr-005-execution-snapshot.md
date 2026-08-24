# ADR-005: Execution Snapshot

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P07

## Context

任务执行过程中配置恰好发布新版本，可能出现前半段用 v10、后半段用 v11，行为不可复现、Trace 无法解释、Eval 失真。

## Constraints

- 热更新不能通过重启 Pod 生效。
- 已开始的 Execution 必须固定配置事实。

## Options

1. 执行中途跟随最新版本（漂移）。
2. 请求进入时一次性解析并固化全部版本集合。

## Decision

**每次请求构建不可变 ExecutionSnapshot**：`agent_version + skill_versions + mcp_versions + plugin_versions + policy_version + model_resolution`。

- 已开始 Execution 固定 Snapshot；新 Execution 使用最新 Published Version。
- Trace 记录所有实际版本，支持回滚、灰度、复现。

## Trade-offs

- 换取行为可复现与 Eval 可信，代价是执行开始前多一次解析（用 L1 cache 控预算）。

## Failure Modes

- 解析与执行之间配置被删除 → Snapshot 指向已 deprecated 版本，按策略 fail closed 或显式提示。
- Cache 命中旧版本 → 版本号/TTL 收敛。

## Validation

- S-R03：单 Execution 版本固定，执行中发布不影响本次执行。

## Revisit Conditions

- 出现必须跨 Execution 共享实时配置的事实。
