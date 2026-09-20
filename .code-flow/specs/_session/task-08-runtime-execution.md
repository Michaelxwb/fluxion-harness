# TASK-004 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-104 | integration | Snapshot builder→PostgreSQL→Executor request | 配置变化不改旧快照；api_key/auth_secret/credential_json 不落 Snapshot/hash 输入；缺认证明确失败 | tests/agent_runtime/test_snapshot_freeze.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_snapshot_freeze.py"] | planned |
