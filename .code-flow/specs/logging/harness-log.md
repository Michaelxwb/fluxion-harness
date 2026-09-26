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

## Conventions

- **业务代码取 logger 的方式**：统一 stdlib `logging.getLogger(__name__)`。`configure_logging` 已把 handler 与脱敏滤镜装在 **root** logger 上，因此**不要**自建 handler/Formatter，也无需（且不应）引入 `muad_logging.get_logger` 作为第二套入口。
- **trace 关联字段口径唯一**：`trace_id/request_id/run_id/conversation_id/platform_user_id/agent_id/snapshot_id/skill_artifact_id/tool_call_id/task_id/schedule_id`（docs/09 §6.1 共 11 个）。未绑定的字段一律显式空串，**禁止省略或伪造**；`set_trace_context` 遇到未知字段名必须报错，不得静默漂移（`packages/api-kit/src/muad_api/context.py`）。
- **脱敏是双通道**：文本 KV（正则键名）与结构化 field 两条路径都要过滤，且覆盖 `bearer`/`basic` 凭据前缀（`packages/logging-kit/src/muad_logging/redaction.py`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
