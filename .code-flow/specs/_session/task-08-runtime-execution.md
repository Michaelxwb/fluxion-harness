# TASK-020 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-120 | integration | Cancel API→PostgreSQL→真实 Redis→执行检查点 | CANCELLING 为响应态；Redis 故障仍读 DB 取消；无活跃 NO_ACTIVE_RUN；跨租户拒绝；终态/CANCEL 事件一次 | tests/agent_runtime/test_cancellation.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_cancellation.py"] | planned |
