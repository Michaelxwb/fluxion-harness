---
id: harness-auth
description: Agent Harness 通用平台规则：auth
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-auth-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/console_platform/test_user_side_relations.py
      -k s04 && uv run pytest -q tests -k schema_parity
    cwd: .
    timeout: 600
---

# harness-auth

## Rules

- [RULE-auth-001] 授权为三层关系：User→Agent（AgentAccessGrant）、Agent→Skill/MCP（Binding）、SELECTED 资源再叠加 SkillUserGrant/McpUserGrant；Effective Capability 公式必须含 `is_deleted=false` 与资源/Agent `enabled`；绑定无启停开关、授权无到期时间；不建三元授权、不做 MCP Tool 级授权；未授权资源不得进入 Prompt/ToolRegistry/Skill Catalog。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
