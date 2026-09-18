---
id: harness-model
description: Agent Harness 通用平台规则：model
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-model-001
  type: manual
  config:
    checklist: 确认 ModelDefinition 无 is_default、Agent 显式选择 enabled model。
    owner: project-owner
---

# harness-model

## Rules

- [RULE-model-001] `model_definition` 不设 `is_default`；每个 Agent 必须显式保存 `model_id`，不存在平台默认模型回退；模型调用只允许 OPENAI 兼容协议。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
