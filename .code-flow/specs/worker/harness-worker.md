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
    - bash
    - -lc
    - uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py
    cwd: .
    timeout: 600
---

# harness-worker

## Rules

- [RULE-worker-001] PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。

## Conventions

- **Parent-Child 批量幂等**：Parent 在**单事务**内创建 Child（带 `root_id`/`parent_id`/`item_key`），Child 幂等键为 `parent:{parent_id}:{item_key}`；partial unique `(parent_id, item_key) WHERE parent_id IS NOT NULL AND is_deleted = false` 保证同一 Child 只创建一次。reclaim 后已有 Child 只等待 fan-in，**不重复 fan-out**。（`application/batch_fanout.py`、`batch_fanin.py`）
- **Final Delivery 去重与模式**：delivery key 固定为 `task:{task_id}:final`（Child 为 `:{child_id}:final`），以 `delivery_key` 去重；`delivery_mode` 仅 `FINAL_ONLY` / `NONE`；HTTP 2xx 但 Gateway 仅占位（`delivered=false`）**不得**置 `SENT`，须按可重试失败退避重投。（`application/task_service.py`、`delivery/service.py`、`infrastructure/models/task.py`）
- **指标归属**：`run_reclaim_total` 属 Runtime（Worker 无 Run 回收路径，其对应指标是 `task_reclaim_total`）；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
