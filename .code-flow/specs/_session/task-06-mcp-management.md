# TASK-004 Spec Context

- Context-SHA256: `5acaf4db8dcc7b1d8cccab5eba0aba64caded4b255f9b009f146ad7c4b84b798`

## Required Rules
- `harness-mcp#RULE-mcp-001`: V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。
  - rule_sha256=ebb42f99f4b33adb713ed05c2ea7e4c5baf48bbd9a387445116a68540f201fae verifier=harness-mcp#RULE-mcp-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.md
- `harness-snapshot#RULE-snapshot-001`: 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash、预算）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。
  - rule_sha256=bed55091b1673e3c29c216171a74d6564324334a61fd657fe075516056e3f271 verifier=harness-snapshot#RULE-snapshot-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.md
- `harness-test#RULE-test-001`: 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。
  - rule_sha256=bab83fe9dee30b1c16035587a06594e3415bad90eb31beb58d329025775136ac verifier=harness-test#RULE-test-001; artifacts=06-mcp-management.backend.design.md,06-mcp-management.md

## Acceptance Contract
| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | E2E | 探针 MCP HTTP、真实 PostgreSQL | revision/hash 变化与 tool_count 来自最新成功 Catalog；未变时 changed:false | tests/console_mcp/test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k refresh_updates_catalog | planned |
| S-03 | E2E | 注册→探针连接测试→发现全链路 | AVAILABLE、revision/hash 更新 | tests/console_mcp/test_mcp_api.py | uv run pytest -q tests/console_mcp/test_mcp_api.py -k register_test_discover_flow | planned |
| E-01 | integration | 探针 tools/list 失败模式 | MCP_DISCOVERY_FAILED + 保留上一成功 Catalog | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k discovery_failed_preserves_catalog | planned |
| E-04 | integration | 探针连接拒绝 | 同 E-01 | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k connection_failed | planned |
| E-05 | integration | 探针超限模式 | 发现失败保留 Catalog | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py -k tool_limit | planned |
| B-02 | integration | 同上全量 | 目录唯一入口语义 | test_discover_api.py | uv run pytest -q tests/console_mcp/test_discover_api.py | planned |
