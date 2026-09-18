---
id: harness-i18n
description: Agent Harness 通用平台规则：i18n
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-i18n-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py
    cwd: .
    timeout: 300
---

# harness-i18n

## Rules

- [RULE-i18n-001] 后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
