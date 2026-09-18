---
id: harness-frontend
description: Agent Harness 通用平台规则：front
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-front-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck
    cwd: .
    timeout: 600
---

# harness-frontend

## Rules

- [RULE-front-001] 前端 API 调用只经 `src/api/`（services）层，组件禁止裸用 axios/fetch；所有文案只使用 i18n key（zh-CN/en-US）；列表/详情遵循 RULE-ui-001 与 RULE-ui-detail-001。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
