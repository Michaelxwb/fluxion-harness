# TASK-005 Spec Context

- Context-SHA256: `e19da46a57c6c50e224779d993d2e8fc9e728cee59f06a496b213462d18434fb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | E2E | Browser、API、网络探测、UI | 三项结果正确；无登录/Session/业务调用痕迹 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-03"` | planned |
| E-02 | integration | API、CredentialResolver、DB | `CREDENTIAL_MISSING`；不读取 Secret Value | tests/console_platform/test_platform_test_api.py（planned） | `uv run pytest -q tests/console_platform/test_platform_test_api.py -k e02` | planned |
