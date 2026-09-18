---
id: harness-im
description: Agent Harness 通用平台规则：im
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-im-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/console_channel
    - tests/gateway
    cwd: .
    timeout: 600
---

# harness-im

## Rules

- [RULE-im-001] 一个逻辑 Agent 可绑定 0..N 个 IM 通道账号；每个 `bot_id` 只路由到一个 Agent；Bot 与 Runtime/Worker Pod 无任何绑定；Gateway 不保存 `agent_id→Pod` 映射。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
