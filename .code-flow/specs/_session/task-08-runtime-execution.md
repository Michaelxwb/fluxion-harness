# TASK-008 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | runner→HookPipeline→真实 Tool handler | 完整一轮顺序匹配 S-05；补足 on_interrupt；未注册 Hook 不干扰；错误/取消的收尾可观测 | tests/agent_core/test_hook_lifecycle.py -k s05（planned） | ["uv","run","pytest","-q","tests/agent_core/test_hook_lifecycle.py","-k","s05"] | planned |
