# TASK-004 Spec Context

- Context-SHA256: `e19da46a57c6c50e224779d993d2e8fc9e728cee59f06a496b213462d18434fb`

## Required Rules
- `harness-secret#RULE-secret-001`: 密钥明文存于各 Owner 表（模型 `api_key`、Bot `secret`、MCP/平台 `auth_secret`、用户/共享 `credential_json`），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。
  - rule_sha256=eb4cdd0b7fa4d7e182cb84beca0488da13386632d61841b12d39045cda60015b verifier=harness-secret#RULE-secret-001; artifacts=04-project-platform.backend.design.md,04-project-platform.frontend.design.md,04-project-platform.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | Browser、API、DB | DB 明文落库；响应不回显；审计无 Secret | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-02"` | planned |
| E-04 | integration | API、Schema、DB | `COMMON_VALIDATION_ERROR`；Secret 零落库 | tests/console_platform/test_credentials_api.py（planned） | `uv run pytest -q tests/console_platform/test_credentials_api.py -k e04` | planned |
