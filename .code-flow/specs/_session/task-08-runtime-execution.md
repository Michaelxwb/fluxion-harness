# TASK-022 Spec Context

- Context-SHA256: `c6eaa1c21e755138c44af3fe82485ef7d1f9d22efcc63249ec83665ab47445cb`

## Required Rules

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| B-122 | integration | 真实 RunService→Executor→LangGraph/SkillContext→DB | 完整一轮含 Tool、Artifact、审计；secret 不进模型消息/输出；停机关闭连接与后台任务；无空实现替代依赖 | tests/agent_runtime/test_runtime_composition.py（planned） | ["uv","run","pytest","-q","tests/agent_runtime/test_runtime_composition.py"] | planned |
