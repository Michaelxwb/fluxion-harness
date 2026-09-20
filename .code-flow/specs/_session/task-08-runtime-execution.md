# TASK-003 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-103 | integration | Runtime HTTP client→本地 Console 契约服务 | 授权错误保持登记 code；缺失 catalog 不能静默放行；模型认证数据只进入内存对象 | tests/agent_runtime/test_console_client.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_console_client.py"] | planned |
