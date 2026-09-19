# TASK-007 Spec Context

- Context-SHA256: `5acaf4db8dcc7b1d8cccab5eba0aba64caded4b255f9b009f146ad7c4b84b798`

## Required Rules
- `harness-ui-detail#RULE-ui-detail-001`: 详情使用 SideSheet：标题与副标题居左，对象级操作与关闭 X 同一行靠右，Tabs 位于其下；关系操作保存后立即影响后续新 Run/Task。
  - rule_sha256=8dcac2de2d48280e57007cf8a1fb4eb70be2741a2d4e2295bbe4252fdc6f60b1 verifier=harness-ui-detail#RULE-ui-detail-001; artifacts=06-mcp-management.frontend.design.md,06-mcp-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | E2E | 真实浏览器、探针 MCP、DB | loading + 工具数/时间刷新 | e2e/tests/mcp-management/mcp-management.spec.ts | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "S-05" | planned |
| S-06 | E2E | Browser→tools API | schema/操作类型展示、无 Tool 级控制 | 同上 | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "S-06" | planned |
| E-06 | E2E | MCP failure→API→UI | Toast 失败 + 保留上一成功 Catalog | 同上 | npm --prefix e2e test -- --config playwright.mcp.config.ts --grep "E-06" | planned |
| E-07 | integration | 源码契约 | 失败不本地删行 + Toast | tests/frontend/test_mcp_user_scope_contract.py | uv run pytest -q tests/frontend/test_mcp_user_scope_contract.py | planned |
| RULE-ui-detail-001 | E2E | 真实浏览器渲染 | SideSheet 布局 | tests/frontend/test_mcp_detail_contract.py | uv run pytest -q tests/frontend/test_mcp_detail_contract.py | planned |
