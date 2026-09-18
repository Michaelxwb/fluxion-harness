---
id: harness-arch
description: Agent Harness 通用平台规则：arch
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-arch-001
  type: manual
  config:
    checklist: 确认部署单元为四个、Runtime/Worker 无状态且不绑定 Pod/bot。
    owner: project-owner
---

# harness-arch

## Rules

- [RULE-arch-001] 固定四个部署单元（console-platform / agent-runtime / agent-worker / im-gateway）；Runtime 与 Worker 无状态、可横向扩展，不绑定 Pod、bot_id 或用户；同一会话可被任意 Pod 执行。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
