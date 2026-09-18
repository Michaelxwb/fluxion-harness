# TASK-002 Spec Context

- Context-SHA256: `e19da46a57c6c50e224779d993d2e8fc9e728cee59f06a496b213462d18434fb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | API、Registry | 未注册 adapter_key → `PLATFORM_ADAPTER_NOT_FOUND` | tests/console_platform/test_platform_adapters_api.py::test_e01_unknown_adapter_key_returns_platform_adapter_not_found（已实现） | `uv run pytest -q tests/console_platform/test_platform_adapters_api.py -k e01` | green |
