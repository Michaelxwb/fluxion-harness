# TASK-001 Spec Context

- Context-SHA256: `2ce739654795d69d34e138cdb3d73b9c6bb89d7b586e55db82bfe810b5e3a607`

## Required Rules
- `harness-data#RULE-data-001`: 产品表统一 `id/is_deleted/create_time/update_time`；软删除唯一约束用 partial unique `WHERE is_deleted=false`；时间统一 `timestamptz`；同 Owner Schema 用物理 FK，跨 Owner Schema 仅逻辑 UUID；JSON 配置用 `jsonb` 且关键查询字段不得只藏在 JSON。
  - rule_sha256=8bf3c6b16730dbdbc47a0d80ed5a61efc67da8bb3ae7f67b7c9a79c72224c863 verifier=harness-data#RULE-data-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-01 | integration | 真实 PostgreSQL + Alembic 迁移 | 两表标准列/timestamptz；partial unique 软删重建；grant 无 expires_at | tests/acceptance/test_mcp_schema_constraints.py | uv run pytest -q tests/acceptance/test_mcp_schema_constraints.py | planned |
