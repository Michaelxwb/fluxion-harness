# TASK-006 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-106 | integration | 两个 Session→PostgreSQL CAS | 竞争只有一个终态；旧 owner/过期执行者不能续约或覆盖终态；WAITING_INPUT 不误扫 | tests/agent_runtime/test_run_leases.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_run_leases.py"] | planned |
