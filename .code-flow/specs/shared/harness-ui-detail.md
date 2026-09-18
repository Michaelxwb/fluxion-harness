---
id: harness-ui-detail
description: Agent Harness 通用平台规则：ui-detail
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-ui-detail-001
  type: manual
  config:
    checklist: 确认详情 SideSheet 标题/副标题左侧，操作与关闭 X 同行靠右，Tabs 在其下。
    owner: project-owner
---

# harness-ui-detail

## Rules

- [RULE-ui-detail-001] 详情使用 SideSheet：标题与副标题居左，对象级操作与关闭 X 同一行靠右，Tabs 位于其下；关系操作保存后立即影响后续新 Run/Task。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
