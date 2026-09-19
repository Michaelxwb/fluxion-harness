# TASK-003 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-rel#RULE-rel-001`: 关系类修改使用单关系 POST/DELETE 并由独立事务完成；禁止用全量 PUT 覆盖整个关系集合。
  - rule_sha256=7e4f40d23f1fc02fcf150db250adfe42c1fea7f36ba01ac150d9c4991f4ac86d verifier=harness-rel#RULE-rel-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | E2E | 真实 HTTP + PostgreSQL + resolve | 绑定即写入；resolve 立即可见 | tests/console_platform/test_agent_skill_bindings.py -k s02 | uv run pytest -q tests/console_platform/test_agent_skill_bindings.py -k s02 | planned |
| E-03 | integration | 真实 DB（Skill.enabled/is_deleted） | 不存在 404；禁用可绑定 + resolve 过滤 | 同上 -k disabled | 同上 | planned |
| RULE-rel-001 | integration | 路由表 + 真实事务 | 仅单关系端点；解除幂等 | 同上 -k idempotent | 同上 | planned |
