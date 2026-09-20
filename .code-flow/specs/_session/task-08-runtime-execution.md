# TASK-005 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-105 | integration | HTTP handler→PostgreSQL unique→run creation | 同 key 同指纹只创建一次；异指纹 COMMON_CONFLICT；不同消息并发 RUN_BUSY；回滚无半成品 | tests/agent_runtime/test_run_idempotency.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_run_idempotency.py"] | planned |
