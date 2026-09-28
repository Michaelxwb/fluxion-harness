---
id: harness-mcp
description: Agent Harness 通用平台规则：mcp
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-mcp-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_mcp/test_mcp_rules.py
    cwd: .
    timeout: 300
checks:
- id: no-tools-list-in-runtime
  type: regex
  pattern: '"tools/list"'
  files: apps/agent-runtime/**
  message: Run 内禁止调用 MCP tools/list；工具定义只能来自冻结 Snapshot 的 definitions
---

# harness-mcp

## Rules

- [RULE-mcp-001] V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。

## Conventions

Runtime 消费冻结 catalog：

- Run 内禁止 `tools/list`；按 Snapshot `definitions` 注册 `mcp::<server_key>::<tool_name>`，catalog revision/hash 随 Snapshot 冻结不漂移。
- 执行经 Streamable HTTP `initialize → tools/call`（带 `Mcp-Session-Id`/`MCP-Protocol-Version`），`auth_secret` 经 API-09 按 server 主键内存读取。
- MCP 调用写 `egress_audit`（target_type=MCP，DENY/ERROR 同样落库），工具执行写 `tool_call_audit`；MCP 返回的 `isError` 不得伪报成功。

✅ 冻结定义 + 真实调用：

```python
adapter.register_catalog(registry=registry, tenant_id=run_context.tenant_id, run_id=run_context.run_id, servers=snapshot_servers)   # definitions 来自 Snapshot
content = await session.call_tool(tool.name, arguments)                # initialize → tools/call
```

❌ 运行内重新发现工具：

```python
tools = await client.post(endpoint, json={"method": "tools/list"})     # 运行内禁止；授权/catalog 会漂移
```

Console 侧 `discover-tools` 的 Catalog 维护与缓存（PG 为权威源）：

- **发现失败必须保留上一成功 Catalog**：失败时置 `connection_status=DISCOVERY_FAILED` 并写 `last_discovery_error`，`tool_catalog_json`/`hash`/`revision` 一概不动（`application/mcp_service.py:426-432`）。
  - 关键实现约束：失败状态必须用**独立事务**持久化（`_write_failure_state`，`application/mcp_service.py:94-106`）——`discover_tools` 随后抛 `AppError`，主事务会被回滚，同事务写等于没写。
  - 机检：`tests/console_mcp/test_discover_api.py:94-122`
- **`revision` 仅在 `tool_catalog_hash` 变化时 +1**：hash 相同则只刷新 `connection_status`/`last_discovered_at`（`application/mcp_service.py:433-445`）。hash 口径 = catalog 稳定键序（按 `name` 排序、`sort_keys=True` + 紧凑分隔符 + `ensure_ascii=False`）的 `sha256:`（`infrastructure/mcp_client.py:43-58`）。revision 是下游缓存的失效依据，无变化即自增会让缓存与冻结快照无谓失效。
- **MCP catalog 存在 Redis 缓存层，PG 是权威源**：端口 `McpCatalogCache`（`application/mcp_ports.py:7-10`）与实现 `RedisMcpCatalogCache`（`infrastructure/mcp_catalog_cache.py`）；discover/delete 后按 revision 失效（`application/mcp_service.py:367,446-447`）。
  - **缓存失效失败不得影响目录事实源**：`_invalidate_cache` 吞掉异常并注释说明（`application/mcp_service.py:369-375`）——DB 已提交才是事实，缓存不可用只影响性能。
- **工具 `effect` 归一化**：`annotations.readOnlyHint=True` → `READ`，`destructiveHint=True` → `DESTRUCTIVE`，否则 `WRITE`（`infrastructure/mcp_client.py:34-40`）；`tool_catalog_json` 元素固定四字段 `name`/`description`/`input_schema`/`effect`，按 `name` 排序（`:43-53`）。
- **单 server 工具数上限走 `mcp_max_tools_per_server`（默认 200）**（`packages/common/src/muad_common/settings.py:23`；`application/mcp_service.py:417` 传入 `list_tools(max_tools=limit)`）。超限时**立即失败**（`McpClientError("protocol", ...)`，`infrastructure/mcp_client.py:150-182`），使无界拉取不可能发生——按既有失败路径落 `DISCOVERY_FAILED` 并保留上一成功 Catalog，而不是截断出一个不完整的 catalog。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
