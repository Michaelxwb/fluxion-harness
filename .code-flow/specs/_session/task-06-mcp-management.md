# TASK-003 Spec Context

- Context-SHA256: `5acaf4db8dcc7b1d8cccab5eba0aba64caded4b255f9b009f146ad7c4b84b798`

## Required Rules
- `harness-secret#RULE-secret-001`: 密钥明文存于各 Owner 表（模型 `api_key`、Bot `secret`、MCP/平台 `auth_secret`、用户/共享 `credential_json`），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）。
  - rule_sha256=eb4cdd0b7fa4d7e182cb84beca0488da13386632d61841b12d39045cda60015b verifier=harness-secret#RULE-secret-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.frontend.design.md,06-mcp-management.md
- `harness-api#RULE-api-001`: Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。
  - rule_sha256=615724c773399b4ce032ae7934787e55501ac643ed0a9d2648e135e5003c8ab6 verifier=harness-api#RULE-api-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.md
- `harness-api#RULE-api-002`: 创建/上传类 POST（导入、可重试提交）支持 `Idempotency-Key` Header：DB 幂等表 partial unique `(tenant_id, idempotency_key, endpoint)` 记录首次响应；请求指纹 = endpoint|关键参数|内容 checksum；同 key 同指纹重放返回首次结果（200 原响应），同 key 不同指纹返回 `COMMON_CONFLICT`。
  - rule_sha256=aa78d9fb6c38b64c15bb312974e019de2044461d3fc2c9ae84969d84022b445a verifier=harness-api#RULE-api-002; artifacts=06-mcp-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-02 | unit | request schema 校验 | MCP_CONFIG_INVALID、拒绝写入 | tests/console_mcp/test_mcp_api.py::config_invalid | uv run pytest -q tests/console_mcp/test_mcp_api.py -k config_invalid | planned |
| B-03 | integration | 真实 HTTP + 真实 PostgreSQL | 不回显明文；审计/日志无明文 | test_mcp_api.py::secret | uv run pytest -q tests/console_mcp/test_mcp_api.py -k secret | planned |
| B-04 | integration | ASGITransport + 真实 PostgreSQL | 封套/分页/聚合计数/软删过滤 | tests/console_mcp/test_mcp_api.py | uv run pytest -q tests/console_mcp/test_mcp_api.py | planned |
| RULE-api-002 | integration | 真实 DB 幂等表 | 同 key 重放首次结果；不同指纹 COMMON_CONFLICT | tests/console_mcp/test_mcp_idempotency.py | uv run pytest -q tests/console_mcp/test_mcp_idempotency.py | planned |
