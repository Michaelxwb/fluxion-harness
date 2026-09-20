---
id: harness-worker
description: Agent Harness 通用平台规则：worker
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-worker-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/agent_runtime
    - --ignore=tests/agent_runtime/test_runner_executor.py
    cwd: .
    timeout: 300
---

# harness-worker

## Rules

- [RULE-worker-001] PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
