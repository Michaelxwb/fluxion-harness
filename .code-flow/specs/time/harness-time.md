---
id: harness-time
description: Agent Harness 通用平台规则：time
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-time-001
  type: command
  config:
    argv:
    - bash
    - -lc
    - uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q
      tests -k schema_parity
    cwd: .
    timeout: 300
---

# harness-time

## Rules

- [RULE-time-001] 数据库统一 `timestamptz` 存储；Console 展示统一 `YYYY-MM-DD HH:mm:ss`；调度时区使用 IANA 时区（如 `Asia/Shanghai`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
