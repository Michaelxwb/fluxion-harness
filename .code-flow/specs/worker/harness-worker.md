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
- **指标是双出口，且存在「未进 `CATALOG`」的计数器**：Worker 的计数既写结构化 `metric` 日志（`metric/amount/total`），也经 `inc_counter` 进 `/metrics`；无 label 的计数走 `increment()`，两出口同时写（`apps/agent-worker/src/muad_agent_worker/metrics.py:1-8,49-57`）。但 `task_claim_total`（`worker/claimer.py:15`）、`delivery_attempt_total`/`delivery_failed_total`（`delivery/service.py:33-34`）、`task_deadline_exceeded_total`（`scheduler/service.py:61`）**不在 `CATALOG`**，因而无流量时不会出现在 `/metrics` 目录中。✅ 无 label 计数用 `increment()`（如需零流量可见须先登记进 `CATALOG`）；❌ 假设所有计数器都能在 `/metrics` 里凭空看到。
- **claim 不止 Task**：Delivery 与 Scheduler 各自持有 `FOR UPDATE SKIP LOCKED` 的取候选路径（`delivery/service.py:141`、`scheduler/service.py:636`），Scheduler 在事务内还会按 `revision`/`next_fire_at` 复检一次 CAS，暂停/删除若已赢得竞态则不返回行（`scheduler/service.py:642-662`）。投递退避为 `BACKOFF_BASE_SEC * 2**delivery_attempts`，且 `delivery_attempts` 在发起请求前自增并提交，崩溃重启不丢退避进度（`delivery/service.py:113-126`）。
- **租约守卫式 CAS**：写任务状态的条件必须同时含 `lease_owner = 本实例` 与 `lease_until > now`——租约失效即失约，避免卡住后恢复的 Worker 用过期结果覆盖别人的执行（`worker/service.py:465-486`）；Schedule 侧口径为「状态判断与写入在同一行锁内完成」（`scheduler/service.py:281-293`）。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
