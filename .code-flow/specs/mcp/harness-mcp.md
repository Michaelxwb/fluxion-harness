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

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
