# TASK-002 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-model#RULE-model-001`: `model_definition` 不设 `is_default`；每个 Agent 必须显式保存 `model_id`，不存在平台默认模型回退；模型调用只允许 OPENAI 兼容协议。
  - rule_sha256=89d599298bbeda3552aa6049fcffb09362b6edb85064a5f1628ef87d281cc2ee verifier=harness-model#RULE-model-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md
- `harness-snapshot#RULE-snapshot-001`: 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash、预算）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。
  - rule_sha256=bed55091b1673e3c29c216171a74d6564324334a61fd657fe075516056e3f271 verifier=harness-snapshot#RULE-snapshot-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md
- `harness-api#RULE-api-002`: 创建/上传类 POST（导入、可重试提交）支持 `Idempotency-Key` Header：DB 幂等表 partial unique `(tenant_id, idempotency_key, endpoint)` 记录首次响应；请求指纹 = endpoint|关键参数|内容 checksum；同 key 同指纹重放返回首次结果（200 原响应），同 key 不同指纹返回 `COMMON_CONFLICT`。
  - rule_sha256=aa78d9fb6c38b64c15bb312974e019de2044461d3fc2c9ae84969d84022b445a verifier=harness-api#RULE-api-002; artifacts=07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 真实 HTTP + 真实 PostgreSQL + runtime_snapshot 行 | revision+1；resolve 新配置；旧快照行不漂移 | tests/console_platform/test_agents_api.py -k revision | uv run pytest -q tests/console_platform/test_agents_api.py -k revision | planned |
| S-05 | E2E | 同上 | 软删后 404/resolve AGENT_NOT_FOUND/历史保留 | 同上 -k soft_delete | 同上 | planned |
| E-01 | integration | 真实 DB revision 列 | REVISION_CONFLICT；key 不可改；响应 {id,revision,update_time} | 同上 -k revision_conflict | 同上 | planned |
| E-05 | integration | 真实 resolve 链路 | AGENT_DISABLED | tests/console_internal/test_resolve_definition_api.py | uv run pytest -q tests/console_internal/test_resolve_definition_api.py -k disabled | planned |
| RULE-api-002 | integration | 真实 DB 幂等表 | 同 key 重放首次结果；不同指纹 COMMON_CONFLICT | tests/console_platform/test_agent_idempotency.py | uv run pytest -q tests/console_platform/test_agent_idempotency.py | planned |
