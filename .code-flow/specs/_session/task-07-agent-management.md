# TASK-007 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-api#RULE-api-001`: Console 与内部 API 使用统一封套：外部 `{code,msg,data,trace_id,request_id,timestamp}`；列表统一 `{items,page,page_size,total}`，`page>=1`、`1<=page_size<=100`；业务只抛 error code，`msg`/`http_status` 只来自 `config/api-messages.yaml`。
  - rule_sha256=615724c773399b4ce032ae7934787e55501ac643ed0a9d2648e135e5003c8ab6 verifier=harness-api#RULE-api-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-04 | integration | 真实 HTTP + PostgreSQL（config_audit_log） | 封套/分页/resource_id 过滤/无 Secret | tests/console_platform/test_audits_api.py | uv run pytest -q tests/console_platform/test_audits_api.py | planned |
