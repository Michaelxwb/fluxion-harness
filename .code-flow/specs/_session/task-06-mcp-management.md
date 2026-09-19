# TASK-005 Spec Context

- Context-SHA256: `5acaf4db8dcc7b1d8cccab5eba0aba64caded4b255f9b009f146ad7c4b84b798`

## Required Rules
- `harness-auth#RULE-auth-001`: 授权为三层关系：User→Agent（AgentAccessGrant）、Agent→Skill/MCP（Binding）、SELECTED 资源再叠加 SkillUserGrant/McpUserGrant；Effective Capability 公式必须含 `is_deleted=false` 与资源/Agent `enabled`；绑定无启停开关、授权无到期时间；不建三元授权、不做 MCP Tool 级授权；未授权资源不得进入 Prompt/ToolRegistry/Skill Catalog。
  - rule_sha256=f3519a0d7c997f4328e45c58d9132e3598a5d56fdd9b57eab3dc7d97865e57bd verifier=harness-auth#RULE-auth-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.md
- `harness-rel#RULE-rel-001`: 关系类修改使用单关系 POST/DELETE 并由独立事务完成；禁止用全量 PUT 覆盖整个关系集合。
  - rule_sha256=7e4f40d23f1fc02fcf150db250adfe42c1fea7f36ba01ac150d9c4991f4ac86d verifier=harness-rel#RULE-rel-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | Grant service、真实 PostgreSQL | 只创建 McpUserGrant；无 expires_at | tests/console_mcp/test_user_scope_api.py | uv run pytest -q tests/console_mcp/test_user_scope_api.py -k user_scope_and_grants | planned |
| E-03 | integration | 真实 HTTP + DB | COMMON_CONFLICT、不重复 | 同上 | uv run pytest -q tests/console_mcp/test_user_scope_api.py -k duplicate_grant | planned |
| B-05 | integration | 真实 HTTP + DB | 单关系/软删重建/分页封套 | 同上 | uv run pytest -q tests/console_mcp/test_user_scope_api.py | planned |
