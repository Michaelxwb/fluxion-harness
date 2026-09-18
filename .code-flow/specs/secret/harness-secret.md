---
id: harness-secret
description: Agent Harness 通用平台规则：secret
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-secret-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_logging_redaction.py
    - tests/acceptance/test_foundation_ops_audit.py
    cwd: .
    timeout: 300
---

# harness-secret

## Rules

- [RULE-secret-001] Secret Value 不进入 DB、Snapshot、Prompt、日志、Audit 或 IM 消息；业务表只保存 `secret_ref`，运行时经 Secret Provider 解析。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
