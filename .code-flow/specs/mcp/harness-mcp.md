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
---

# harness-mcp

## Rules

- [RULE-mcp-001] V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
