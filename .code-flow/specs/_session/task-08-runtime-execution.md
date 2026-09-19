# TASK-028 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-128 | integration | Console credentials service→真实 PostgreSQL Owner 表 | 服务身份、可信 tenant/actor 与资源归属校验；旧 Grant 撤销不替换冻结能力；密钥轮换读取新值，缺密钥 CREDENTIAL_MISSING；日志/公开返回不泄漏；不返回新模型参数/catalog | tests/console_internal/test_runtime_credentials.py（planned） | ["uv","run","pytest","-q","tests/console_internal/test_runtime_credentials.py"] | planned |
