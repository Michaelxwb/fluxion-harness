# TASK-005 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-04 | E2E | 真实 HTTP + PostgreSQL + resolve | 授权后可用/撤销后拒绝/快照不漂移 | tests/console_platform/test_agent_user_grants_api.py -k s04 | uv run pytest -q tests/console_platform/test_agent_user_grants_api.py -k s04 | planned |
| E-04 | integration | 真实 DB partial unique | 幂等恢复；无重复有效行 | 同上 -k idempotent | 同上 | planned |
