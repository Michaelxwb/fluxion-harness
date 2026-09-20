# TASK-009 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | integration | 真实 CanonicalEvent/Memory/Artifact→ContextBuilder→LLM request | 仅裁剪 request；事件 append-only；tenant+user+enabled 过滤；大结果 preview | tests/agent_runtime/test_context_memory.py -k s08（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_context_memory.py","-k","s08"] | planned |
