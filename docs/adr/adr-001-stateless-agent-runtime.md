# ADR-001: Stateless Agent Runtime

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P02
- **Replaces**: 旧 `muad-openclaw` Agent/Pod 持有事实状态的设计

## Context

旧项目 Agent 与配置、用户状态、Session/Memory、Credential 强绑定。请求落到不同 Pod 会出现不一致，Pod 重启变成状态迁移问题。

## Constraints

- 生产环境多 Pod 无状态横向扩展；Pod 可随时丢弃。
- 本地开发零基础设施可运行（SQLite）。
- 不引入 Redis/PostgreSQL 作为 Runtime 启动前提。

## Options

1. Agent Pod 本地持有全部状态（旧模型）。
2. Stateless Runtime：AgentDefinition 配置入 Registry，Session/Memory/Credential/Workflow 状态全部外置。
3. 纯函数式执行，完全不缓存（无 L1 cache）。

## Decision

**Stateless Runtime。** Agent Runtime Pod 是可丢弃计算资源；状态归属：

```text
RuntimeProfile 配置 → Registry
Session           → Session Store
Memory            → Memory Store
Credential        → Secret Store
Workflow State    → Workflow Engine（业务接入层）
```

Runtime 只负责 `Resolve → Plan → Reason → Execute → Return`。L1 Cache 允许存在，但不是事实源。

## Trade-offs

- 换取横向扩展与故障恢复能力，代价是每请求增加一次 Registry 解析（用 ExecutionSnapshot + L1 cache 控制）。
- 需要显式的 Store/Registry Contract，比本地文件状态更重。

## Failure Modes

- Store/Registry 故障 → Runtime 无法解析配置 → fail closed。
- Cache 与 Registry 不一致 → 用版本号/TTL/revocation event 收敛。

## Validation

- S-R05 / B-R03：多 Pod 等价解析；Pod 删除无事实状态丢失。
- 性能 NFR：Resolver L1 P95≤5ms、Snapshot P95≤20ms。

## Revisit Conditions

- 出现必须粘滞在 Pod 内的强实时状态且无法外置。
