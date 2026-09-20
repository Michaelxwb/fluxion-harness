# TASK-017 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-06 | integration | ModelGateway→fake provider→真实审计 DB | 429 Retry-After:1 后成功；记录 attempt/retry_reason；Run 不失败 | tests/agent_runtime/test_model_recovery.py -k s06（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","s06"] | planned |
| E-06 | integration | ModelGateway→fake provider→PostgreSQL | 429/5xx/reset/timeout 超重试或 deadline 后 FAILED/MODEL_UNAVAILABLE；逐 attempt 审计；取消终止退避 | tests/agent_runtime/test_model_recovery.py -k e06（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_model_recovery.py","-k","e06"] | planned |
