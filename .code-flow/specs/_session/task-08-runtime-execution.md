# TASK-010 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-110 | integration | Memory service→PostgreSQL | 跨 tenant/user 无泄漏；禁用/删除不读；来源与版本可追溯；不把实时业务事实自动写长期 Memory | tests/agent_runtime/test_memory_service.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_memory_service.py"] | planned |
