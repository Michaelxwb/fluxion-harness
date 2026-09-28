---
id: harness-data
description: Agent Harness 通用平台规则：data
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-data-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests
    - -k
    - schema_parity
    cwd: .
    timeout: 600
# 规则正文在根 CLAUDE.md 的 Core Principles（租户内存在性判断），此处只放机检。
checks:
- id: no-global-count-in-tenant-check
  type: regex
  pattern: 'count_all\('
  files: apps/console-platform/backend/**
  message: 租户内的存在性判断禁止全库计数；必须按租户限定（count(tenant_id)）——全库判定会让任一租户有账号就掩盖「默认租户无账号 ⇒ 无法登录」的静默故障
---

# harness-data

## Rules

- [RULE-data-001] 产品表统一 `id/is_deleted/create_time/update_time`；软删除唯一约束用 partial unique `WHERE is_deleted=false`；时间统一 `timestamptz`；同 Owner Schema 用物理 FK，跨 Owner Schema 仅逻辑 UUID；JSON 配置用 `jsonb` 且关键查询字段不得只藏在 JSON。

## Conventions

- **跨 Owner Schema 的只读聚合**：允许直接读取其它 Owner 的表（如 `task.*`、`runtime.*`）做**只读聚合投影**，条件是：① 只读、不写任何表；② 每条查询都带 `tenant_id` 与 `is_deleted = false`（或该表的软删等价条件）；③ 名称类字段在同一 SQL 内 JOIN 批量补齐，不做逐行关联（N+1）。既有两例：`audit_query_repository`（11-audit-observability）与 `overview_query_repository`（12-overview-dashboard）。**不建宽表、不建物化视图**，跨 Schema 一律逻辑引用（UUID）、不建物理 FK。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
