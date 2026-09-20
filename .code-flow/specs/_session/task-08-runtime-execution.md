# TASK-023 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-123 | integration | 真实 HTTP 进程→PG/Redis/NFS | 进程实际独立；探针可记录请求/注入受控故障；禁 dependency_overrides/mock 业务服务；清理可重复 | tests/acceptance/runtime/test_environment.py（planned） | ["uv","run","pytest","-q","tests/acceptance/runtime/test_environment.py"] | planned |
