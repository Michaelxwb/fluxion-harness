# TASK-013 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-113 | integration | ToolRegistry→真实 handler→审计 port | 错误 schema/拒绝策略不调用 handler；prepared_args_hash 稳定且脱敏；成功/失败各一次终态；不吞异常 | tests/agent_core/test_tool_execution_pipeline.py（planned） | ["uv","run","pytest","-q","tests/agent_core/test_tool_execution_pipeline.py"] | planned |
