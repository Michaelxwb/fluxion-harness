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
  type: manual
  config:
    checklist: 确认标准列、软删除 partial unique、timestamptz、同 Owner Schema 物理 FK/跨 Schema
      逻辑 UUID。
    owner: project-owner
---

# harness-data

## Rules

- [RULE-data-001] 产品表统一 `id/is_deleted/create_time/update_time`；软删除唯一约束用 partial unique `WHERE is_deleted=false`；时间统一 `timestamptz`；同 Owner Schema 用物理 FK，跨 Owner Schema 仅逻辑 UUID；JSON 配置用 `jsonb` 且关键查询字段不得只藏在 JSON。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
