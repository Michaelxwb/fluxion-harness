# TASK-009 Spec Context

- Context-SHA256: `65d367d5c7715054c142f7721a339cb94555cee14053e2d8ca26cddc7f4e8743`

## Required Rules
- `harness-ui-detail#RULE-ui-detail-001`: 详情使用 SideSheet：标题与副标题居左，对象级操作与关闭 X 同一行靠右，Tabs 位于其下；关系操作保存后立即影响后续新 Run/Task。
  - rule_sha256=8dcac2de2d48280e57007cf8a1fb4eb70be2741a2d4e2295bbe4252fdc6f60b1 verifier=harness-ui-detail#RULE-ui-detail-001; artifacts=07-agent-management.frontend.design.md,07-agent-management.md
- `harness-test#RULE-test-001`: 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。
  - rule_sha256=bab83fe9dee30b1c16035587a06594e3415bad90eb31beb58d329025775136ac verifier=harness-test#RULE-test-001; artifacts=07-agent-management.backend.design.md,07-agent-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | E2E | 真实浏览器 + 后端 + DB | revision+1 展示 | e2e spec -k S-07 | npm --prefix e2e test -- --config playwright.agent.config.ts --grep "S-07" | planned |
| S-08 | E2E | 同上 | 绑定即出现；无保存/启停 | 同上 | 同上 --grep "S-08" | planned |
| S-09 | E2E | Browser→audits API | 最近运行列表/空态 | 同上 | 同上 --grep "S-09" | planned |
| S-11 | E2E | 同上 | 解除/再绑定无启停开关 | 同上 | 同上 --grep "S-11" | planned |
| S-12 | E2E | 同上 | 双 bot 同 Agent 无 Pod 信息 | 同上 | 同上 --grep "S-12" | planned |
| E-07 | E2E | revision conflict→UI | Modal 保留提示 | 同上 | 同上 --grep "E-07" | planned |
| E-08 | E2E | bot conflict→UI | 表单保留提示 | 同上 | 同上 --grep "E-08" | planned |
| E-10 | E2E | 目标不存在→Toast | Tab 状态不变 | 同上 | 同上 --grep "E-10" | planned |
| RULE-ui-detail-001 | E2E | 真实浏览器渲染 | SideSheet 布局/5 Tabs | tests/frontend/test_agent_detail_contract.py | uv run pytest -q tests/frontend/test_agent_detail_contract.py | planned |
| RULE-test-001 | E2E | 全链路无 mock 声明 | 边界清单显式化 | e2e spec 模块头 | npm --prefix e2e test -- --config playwright.agent.config.ts | planned |
