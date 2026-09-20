# TASK-019 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-119 | integration | Resume API→PostgreSQL→LangGraph | 仅一个并发 resume 成功；失败不追加消息；原 run_id、resumed=true、seq 延续；终态不再执行 | tests/agent_runtime/test_resume_transactions.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_resume_transactions.py"] | planned |
