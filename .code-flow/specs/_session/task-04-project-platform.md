# TASK-010 Spec Context

- Context-SHA256: `e19da46a57c6c50e224779d993d2e8fc9e728cee59f06a496b213462d18434fb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | E2E | Browser、Secret API、DB | 保存后仅「已配置」；DB 明文；响应无明文 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-06"` | planned |
| S-07 | E2E | Browser、API、网络探测、UI | 三项结果展示；无平台登录/Session/业务调用 | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "S-07"` | planned |
| E-06 | E2E | API、网络探测、UI | `UNREACHABLE` 与失败原因；无 Secret/Session | e2e/tests/project-platform/platform.spec.ts（planned） | `npm --prefix e2e test -- --config playwright.platform.config.ts --grep "E-06"` | planned |
| E-07 | integration | Schema 校验 API、Form | 字段级错误 + 本地化提示；请求未提交 | tests/frontend/test_platform_credential_form_contract.py（planned） | `uv run pytest -q tests/frontend/test_platform_credential_form_contract.py` | planned |
