# TASK-004 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-auth#RULE-auth-001`: 授权为三层关系：User→Agent（AgentAccessGrant）、Agent→Skill/MCP（Binding）、SELECTED 资源再叠加 SkillUserGrant/McpUserGrant；Effective Capability 公式必须含 `is_deleted=false` 与资源/Agent `enabled`；绑定无启停开关、授权无到期时间；不建三元授权、不做 MCP Tool 级授权；未授权资源不得进入 Prompt/ToolRegistry/Skill Catalog。
  - rule_sha256=f3519a0d7c997f4328e45c58d9132e3598a5d56fdd9b57eab3dc7d97865e57bd verifier=harness-auth#RULE-auth-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | 真实 HTTP + PostgreSQL + resolve | 解除后立即不可见；再绑恢复 | tests/console_platform/test_agent_mcp_bindings.py -k s06 | uv run pytest -q tests/console_platform/test_agent_mcp_bindings.py -k s06 | planned |
| B-02 | integration | 真实 resolve 链路（全公式矩阵） | mcp_servers 按公式过滤；EffectiveSkill 不回归 | tests/console_internal/test_resolve_definition_api.py -k mcp | uv run pytest -q tests/console_internal/test_resolve_definition_api.py -k mcp | planned |
| RULE-auth-001 | integration | 同上 | 无三元授权；绑定即生效无开关 | 同上 | 同上 | planned |
