# TASK-006 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-im#RULE-im-001`: 一个逻辑 Agent 可绑定 0..N 个 IM 通道账号；每个 `bot_id` 只路由到一个 Agent；Bot 与 Runtime/Worker Pod 无任何绑定；Gateway 不保存 `agent_id→Pod` 映射。
  - rule_sha256=8761e57a0e043f3c0dc2d97252f6603644a6299b176953b6656309725e901429 verifier=harness-im#RULE-im-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md
- `harness-secret#RULE-secret-001`: 密钥明文存于各 Owner 表（模型 `api_key`、Bot `secret`、MCP/平台 `auth_secret`、用户/共享 `credential_json`），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。
  - rule_sha256=eb4cdd0b7fa4d7e182cb84beca0488da13386632d61841b12d39045cda60015b verifier=harness-secret#RULE-secret-001; artifacts=07-agent-management.backend.design.md,07-agent-management.frontend.design.md,07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | 真实 HTTP + PostgreSQL | 双 bot 同 agent；secret 落库不回显 | tests/console_platform/test_agent_channels_api.py -k s03 | uv run pytest -q tests/console_platform/test_agent_channels_api.py -k s03 | planned |
| E-02 | integration | 真实 partial unique | COMMON_CONFLICT + message_args；不重绑 | 同上 -k bot_conflict | 同上 | planned |
| E-06 | integration | 真实 DB 查询 | BOT_NOT_FOUND；数据不变 | 同上 -k bot_not_found | 同上 | planned |
| B-03 | integration | 真实 DB 列 + 审计表 | 明文仅在 bot_account.secret | 同上 -k secret | 同上 | planned |
