---
id: harness-log
description: Agent Harness 通用平台规则：log
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-log-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_logging.py
    - tests/test_logging_redaction.py
    - tests/acceptance/test_foundation_logging.py
    cwd: .
    timeout: 300
---

# harness-log

## Rules

- [RULE-log-001] 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`；日志按 `service/YYYY-MM-DD.log` 输出 JSON，自动携带 `trace_id/request_id/tenant_id`，并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
