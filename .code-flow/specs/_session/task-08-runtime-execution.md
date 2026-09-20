# TASK-029 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-129 | integration | Console resolve-egress service→真实 PostgreSQL 平台/凭据表 | RUN/TASK 与 PLATFORM_SERVICE/HTTP/MCP 类型合法；四类凭据策略正确；租户/用户隔离；DENY/FORBIDDEN、缺凭据和适配器错误明确；不执行平台登录、不返回越权凭据 | tests/console_internal/test_resolve_egress_api.py（planned） | ["uv","run","pytest","-q","tests/console_internal/test_resolve_egress_api.py"] | planned |
