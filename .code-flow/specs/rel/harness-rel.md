---
id: harness-rel
description: Agent Harness 通用平台规则：rel
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-rel-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_platform/test_user_side_relations.py
    cwd: .
    timeout: 300
---

# harness-rel

## Rules

- [RULE-rel-001] 关系类修改使用单关系 POST/DELETE 并由独立事务完成；禁止用全量 PUT 覆盖整个关系集合。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
