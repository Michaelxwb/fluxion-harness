---
id: harness-snapshot
description: Agent Harness 通用平台规则：snapshot
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-snapshot-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/agent_runtime -k "executor or resolve"
    cwd: .
    timeout: 600
---

# harness-snapshot

## Rules

- [RULE-snapshot-001] 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash、预算）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
