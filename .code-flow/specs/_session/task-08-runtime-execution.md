# TASK-016 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-116 | integration | Snapshot→ToolRegistry→真实本地 MCP HTTP 服务 | 运行内不调用 tools/list；Server 授权粒度不变；catalog revision/hash 不漂移；Tool/Egress 双审计完整 | tests/agent_runtime/test_mcp_execution.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_mcp_execution.py"] | planned |
