# TASK-001 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-data#RULE-data-001`: 产品表统一 `id/is_deleted/create_time/update_time`；软删除唯一约束用 partial unique `WHERE is_deleted=false`；时间统一 `timestamptz`；同 Owner Schema 用物理 FK，跨 Owner Schema 仅逻辑 UUID；JSON 配置用 `jsonb` 且关键查询字段不得只藏在 JSON。
  - rule_sha256=8bf3c6b16730dbdbc47a0d80ed5a61efc67da8bb3ae7f67b7c9a79c72224c863 verifier=harness-data#RULE-data-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | integration | 真实 PostgreSQL + Alembic 迁移 | 五表标准列/timestamptz/partial unique；binding 无 enabled；grant 无 expires_at | tests/acceptance/test_agent_schema_constraints.py | uv run pytest -q tests/acceptance/test_agent_schema_constraints.py | planned |
