---
id: harness-worker
description: Agent Harness 通用平台规则：worker
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-worker-001
  type: manual
  config:
    checklist: 确认 PG 为 Task/Schedule/lease 权威源、Redis 仅 hint、TaskType 仅 SKILL/BATCH。
    owner: project-owner
---

# harness-worker

## Rules

- [RULE-worker-001] PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
