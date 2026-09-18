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
  type: manual
  config:
    checklist: 确认统一 logging-kit、LOG_DIR、按 service/日期落盘、trace_id/request_id 与脱敏。
    owner: project-owner
---

# harness-log

## Rules

- [RULE-log-001] 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`；日志按 `service/YYYY-MM-DD.log` 输出 JSON，自动携带 `trace_id/request_id/tenant_id`，并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
