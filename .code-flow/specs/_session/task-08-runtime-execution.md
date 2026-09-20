# TASK-021 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-121 | integration | Executor event stream→SSE→持久化事件 | resume 不从 1 重排；token/工具事件实时转发；heartbeat 注释帧；断流不伪造终态 | tests/agent_runtime/test_sse.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_sse.py"] | planned |
