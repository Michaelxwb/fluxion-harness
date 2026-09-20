# TASK-002 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-102 | integration | EventWriter→PostgreSQL | 并发 append 不重号；回滚不留下事件；历史查询顺序稳定；序号跨 Run/resume 保持单调 | tests/agent_runtime/test_run_events.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_run_events.py"] | planned |
