# TASK-018 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-118 | integration | LangGraph→PG checkpoint/run_interrupt | 进程重建仍可定位等待点；触发 on_interrupt；非授权执行者不能推进；业务事实不依赖进程内存 | tests/agent_runtime/test_interrupt_checkpoint.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_interrupt_checkpoint.py"] | planned |
