# ADR-004: Registry Store 抽象（SQLite dev / PostgreSQL prod）

- **Status**: Accepted
- **Date**: 2026-08-23
- **Problem Driver**: P04

## Context

本地开发希望 clone 后零基础设施直接运行；生产需要多 Pod、一致性、多租户、版本、发布、审计、热更新。两套存储需求冲突。

## Constraints

- 同一套 Resource Schema、Migration、Repository/RegistryStore Contract、版本语义。
- YAML 仅 import/export，不是运行期事实源。
- 本地不强制 PostgreSQL/Redis/K8s；生产以 PostgreSQL 为事实源、Redis Streams 发配置事件（PostgreSQL Outbox 保证可靠投递）。

## Options

1. 仅 PostgreSQL，本地也装 PG。
2. FileStore(YAML) 本地 + DBStore 生产，两套语义。
3. Store SPI：SQLite dev / PostgreSQL prod 共享同一 Contract。

## Decision

**RegistryStore SPI**，默认 `SQLiteRegistryStore`（dev）与 `PostgreSQLRegistryStore`（prod）：

```text
Runtime → RegistryStore
           ├── SQLiteRegistryStore
           └── PostgreSQLRegistryStore
```

本地开发：`Console + Runtime → SQLite`；生产：`Console + Runtime → PostgreSQL`。Repository Contract Test 对两种 Store 跑同一套。

## Trade-offs

- 换取本地零依赖与生产一致性，代价是必须显式处理两种数据库差异（JSON 序列化语义、并发发布唯一约束、Migration 双跑、隔离级别以 PG 为准）。

## Failure Modes

- SQLite-only 路径与 PG 行为不一致 → 通过 Contract Test 双跑 + 生产压测以 PG 为准。
- YAML 被当作事实源 → 用 import/export 显式边界 + S-R07 验证。

## Validation

- S-R07：RegistryStore Contract 双实现一致。
- 契约测试脚本在 SQLite 与 PostgreSQL（testcontainers）上全绿。

## Revisit Conditions

- 出现 SQLite/PG 行为差异无法通过 Contract 层收敛的持久化能力。
