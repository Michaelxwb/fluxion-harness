# TASK-014 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-114 | integration | HTTP resolve→PlatformAdapter→真实 Redis | 四类 credential_mode 正确；tenant/actor/version/adapter 隔离；adapter 变更旧 Session 失效；无独立 refresh SPI | tests/sdk/test_runtime_platform_session.py（planned） | ["uv","run","pytest","-q","tests/sdk/test_runtime_platform_session.py"] | planned |
