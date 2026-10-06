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

- [RULE-worker-001] PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。**deadline 是领取与收尾两侧的硬条件**：claim 排除 `deadline_at <= now` 的行（过期任务绝不执行），执行跨过 deadline 也不得写 `COMPLETED`，同样以 `FAILED(TASK_DEADLINE_EXCEEDED)` 收尾（`worker/claimer.py:20`、`worker/service.py:491`）。

## Conventions

- **Parent-Child 批量幂等**：Parent 在**单事务**内创建 Child（带 `root_id`/`parent_id`/`item_key`），Child 幂等键为 `parent:{parent_id}:{item_key}`；partial unique `(parent_id, item_key) WHERE parent_id IS NOT NULL AND is_deleted = false` 保证同一 Child 只创建一次。reclaim 后已有 Child 只等待 fan-in，**不重复 fan-out**。（`application/batch_fanout.py`、`batch_fanin.py`）
- **父子事务只允许「父 → 子」的加锁顺序**：取消路径先锁 Parent 再动 Child，因此子任务终态事务也必须在子行 CAS **之前**取父行锁（`worker/service.py:533` 的 `_lock_parent`），否则两条路径互为循环等待，PostgreSQL 判死锁并回滚一方（完成或取消的结果一起丢）；`reclaim_expired` 同理分两段提交、根任务在前。✅ 先 `SELECT … FOR UPDATE` 父行再 CAS 子行；❌ 先 UPDATE 子行、到 `settle_child` 才锁父行。
- **扇出前必须证明「本次执行仍持有父任务」**：父行锁内四格一起看——`status=RUNNING`、`cancel_requested=false`、`lease_until > now`、`lease_owner` 与本执行一致；已取消/已失约的父任务**不得**建出可执行的 Child（`application/batch_fanout.py:60`）。✅ `_require_live_owner(locked, claimed, moment)`；❌ 只按 `id` 加锁就插 Child。
- **fan-in 接受父任务的全部非终态（含 `QUEUED`）**：`reclaim` 会把崩溃的父任务置回 `QUEUED`，此时最后一个 Child 终态若不肯聚合，父任务再没有下一个事件可触发 fan-in，只能停到 deadline；已请求取消的父任务除外（那要由它自己的取消路径收尾成 `CANCELLED`，不能被扇入改写成成功/失败）（`application/batch_fanin.py:170`）。
- **取消是一次原子写入**：一条 UPDATE 同时落下「任何非终态都置 `cancel_requested=true`」与「`QUEUED/WAITING` 顺带置 `CANCELLED`（并释放 lease）」；`RUNNING` 保持状态由 Worker 在检查点协作停止（`application/task_service.py:316`）。✅ 状态与标记同一语句；❌ 先读状态再挑一条 CAS——取消读到 `QUEUED`、claim 抢先把行置成 `RUNNING` 时两条 CAS 都不成立，接口回 `RUNNING/cancel_requested=false` 且库里没有取消标记，用户的取消被静默丢弃。
- **Final Delivery 去重与模式**：delivery key 固定为 `task:{task_id}:final`（Child 为 `:{child_id}:final`），以 `delivery_key` 去重；`delivery_mode` 仅 `FINAL_ONLY` / `NONE`。**可投递状态只有 `PENDING`**，`FAILED` 是终态（4xx / 路由缺失 / 次数耗尽）：可重试失败写回 `PENDING`，否则「还在退避中」和「已经没救了」在库里长得一样，耗尽的租户会永远占着跨租户扫描名额（`delivery/service.py:45,324`）。**HTTP 2xx 不等于送达**：只有封套里**明确** `data.delivered == true` 才置 `SENT`，解析不了按可重试失败退避重投（`delivery/client.py:61`）。✅ `{"accepted": true, "delivered": true}` ⇒ SENT；❌ 非法 JSON / 缺 `delivered` 字段也当成功（结果再也不会重投，用户就是收不到）。
- **投递队列跨租户，必须公平且互不连坐**：按 `(最早待投递记录的 create_time, tenant_id)` 的**前进游标**逐租户取快照（`delivery/service.py:232`），单租户取快照失败只跳过它自己（`delivery/service.py:145`）；预留（发送前自增 `delivery_attempts` 并提交）超过 `DELIVERY_RESERVATION_LEASE_SEC` 仍未回执的，按「那次尝试随进程没了」结算成 `FAILED`，不留永远无法再被领取的 `PENDING`（`delivery/service.py:270`）。✅ 固定前缀之外的租户每轮都会被看到；❌ 取「最早的前 N 个」且失败即抛——前 N 个选不出候选或读不到设置时，后面的租户永远轮不到。
- **回收同样受重试预算与退避约束**：`attempt >= max_attempts` 的崩溃任务终态 `FAILED(TASK_ATTEMPTS_EXHAUSTED)`，其余置回 `QUEUED` 并退避 `RETRY_BACKOFF_BASE_SEC * 2**(attempt-1)`（`worker/service.py:307,347`）。
- **指标归属**：`run_reclaim_total` 属 Runtime（Worker 无 Run 回收路径，其对应指标是 `task_reclaim_total`）；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志。
- **指标是双出口，且存在「未进 `CATALOG`」的计数器**：Worker 的计数既写结构化 `metric` 日志（`metric/amount/total`），也经 `inc_counter` 进 `/metrics`；无 label 的计数走 `increment()`，两出口同时写（`apps/agent-worker/src/muad_agent_worker/metrics.py:1-8,49-57`）。但 `task_claim_total`（`worker/claimer.py:15`）、`delivery_attempt_total`/`delivery_failed_total`（`delivery/service.py:33-34`）、`task_deadline_exceeded_total`（`scheduler/service.py:61`）**不在 `CATALOG`**，因而无流量时不会出现在 `/metrics` 目录中。✅ 无 label 计数用 `increment()`（如需零流量可见须先登记进 `CATALOG`）；❌ 假设所有计数器都能在 `/metrics` 里凭空看到。
- **claim 不止 Task**：Delivery 与 Scheduler 各自持有 `FOR UPDATE SKIP LOCKED` 的取候选路径（`delivery/service.py:141`、`scheduler/service.py:636`），Scheduler 在事务内还会按 `revision`/`next_fire_at` 复检一次 CAS，暂停/删除若已赢得竞态则不返回行（`scheduler/service.py:642-662`）。投递退避为 `delivery_backoff_base_sec`（设置项，**生产默认 5**）× `2**delivery_attempts`，且 `delivery_attempts` 在发起请求前自增并提交，崩溃重启不丢退避进度（`delivery/service.py:113-126`）。
- **租约守卫式 CAS**：写任务状态的条件必须同时含 `lease_owner = 本实例` 与 `lease_until > now`——租约失效即失约，避免卡住后恢复的 Worker 用过期结果覆盖别人的执行（`worker/service.py:669`）；Schedule 侧口径为「状态判断与写入在同一行锁内完成」（`scheduler/service.py:281-293`）。**续租同口径**（`worker/service.py:755`）：只认**当前租约仍有效**，否则卡顿超过租期后一次心跳就能把失效的执行权续到未来。✅ 续租条件含 `lease_until > now`；❌ 只看 `status`/`lease_owner` 就续租。
- **执行与租约维护共同受监督**：`run_once` 等的是「执行结束」与「心跳退出」中的先者，心跳先退出（续租连续失败或失约）即叫停本地执行（`worker/service.py:170`）。续租的瞬时 DB 错误有界重试，重试用尽就退出心跳；**绝不**让执行器脱离租约监控——终态 CAS 拦得住写库，拦不住已经发生的外部副作用。
- **Skill 结果按协议强校验**：`result` / `wait` / `wait.external_ref` / `wait.not_before` 形状不对即 `TaskExecutionError(SKILL_RESULT_INVALID)`（**确定性**失败，不消耗重试预算），并且解析本身处在 `run_once` 的统一异常处理内——否则类型错会冒泡出去，任务停在没有心跳的 `RUNNING` 等回收、然后重跑同一个确定性错误（`worker/execution_outcomes.py`）。
- **deadline sweep 有独立循环**：Scheduler 的到期触发与 deadline sweep 是两个互相独立的受监督循环（`scheduler/service.py:458`），一轮慢的 Schedule 解析不得把 sweep 推后整轮——`task_deadline_sweep_interval_sec` 必须是实际最大延迟。✅ `asyncio.gather(fire_forever, sweep_forever)`；❌ 串在同一个 `while` 里先 `await run_due()` 再 sweep。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
