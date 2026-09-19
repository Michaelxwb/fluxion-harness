# TASK-001 Spec Context

- Context-SHA256: `7aaa8221a3b0a4d313492e22e4c272e4e7e1b28bd3fa8f545cac4fa98bdc5dc2`

## Required Rules
- `harness-data#RULE-data-001`: 产品表统一 `id/is_deleted/create_time/update_time`；软删除唯一约束用 partial unique `WHERE is_deleted=false`；时间统一 `timestamptz`；同 Owner Schema 用物理 FK，跨 Owner Schema 仅逻辑 UUID；JSON 配置用 `jsonb` 且关键查询字段不得只藏在 JSON。
  - rule_sha256=8bf3c6b16730dbdbc47a0d80ed5a61efc67da8bb3ae7f67b7c9a79c72224c863 verifier=harness-data#RULE-data-001; artifacts=08-runtime-execution.backend.design.md,08-runtime-execution.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-101 | integration | PostgreSQL migration→ORM | partial unique 阻止双活；同 Owner FK、timestamptz、jsonb 与 ORM 一致；跨 Owner 不建 FK | tests/agent_runtime/test_runtime_schema_parity.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_schema_parity.py"] | planned |
| RULE-data-001 | integration | PostgreSQL migration→ORM＋原 verifier 边界 | partial unique 阻止双活；同 Owner FK、timestamptz、jsonb 与 ORM 一致；跨 Owner 不建 FK；原 verifier 全部通过 | 原 verifier＋tests/agent_runtime/test_runtime_schema_parity.py（planned） | ["bash","-lc","'uv' 'run' 'pytest' '-q' 'tests' '-k' 'schema_parity' && uv run pytest -q tests/agent_runtime/test_runtime_schema_parity.py"] | planned |
