---
{
  "description": "任务、调度与投递",
  "next_rule": 6
}
---

# harness-worker

## Rules

- [RULE-worker-001] PostgreSQL 是 Schedule、Fire、Task Execution 与 lease 的唯一权威，Redis 仅作独立 wake-up/cancel hint。任务执行类型与等待状态由共享 contracts 持有，Reminder、Skill、Agentic 与内部 Batch 使用同一执行权模型；等待释放执行 lease，恢复同一 Execution 不消耗业务重试。Scheduler 在锁外准入、锁内复检，原子写唯一 occurrence Fire、冻结 Execution 与调度进度，Fire 不持有第二套 claim/lease。Worker 使用数据库 claim 与 owner/epoch/有效 lease CAS，按 Schedule 策略原子控制根执行并发及顺序；全局遵守 operation→Schedule→Fire→任务树→事件/发件锁序。claim 与收尾均执行绝对 deadline，过期任务不得执行或提交成功；取消、回收、等待续接及终态共享原子状态/事件边界。

- [RULE-001] 异步工具调用以租户、owner 与真实工具调用身份唯一，输入指纹用于同身份冲突判断，不能参与唯一身份以放行变参重试。提交与结果必须可持久重放；等待前检查结果就绪，按每个调用保存独立 receipt 与消费水位，结果乱序或重复不得丢失或重复消费。持久收件箱与数据库补扫保证恢复正确性，独立唤醒 hint 仅降低延迟；终态收到迟到结果不得恢复执行，但可幂等物化为会话事实（`BACKGROUND_RESULT`）供下一轮读取；物化失败、会话已删除或跳过必须显式可观测。

- [RULE-002] 人工交互是 Execution 所属的持久待处理状态；问题、检查点、进入等待与释放执行权必须原子提交，问题有效期不得超出执行 deadline。回答须同时核对交互 OPEN、执行确在等待、取消标记与有效期，再原子保存回答并排队续接；相同回答幂等，变化冲突。仅停止未来调度时保留已开始执行与 OPEN 问题，终止全部或超时时才原子关闭；任何回答或迟到结果都不得复活终态。

- [RULE-003] 投递意图与业务执行状态分离：PUSH 的最终结果及人工问题先持久化独立投递记录，再以稳定业务键、持久尝试和有效发送租约驱动；只有接收方明确证明送达才能确认成功，失败不得改写业务执行终态。NONE 的定时结果与问题只由任务中心读取，禁止产生投递、会话消息或通知未读。投递采用创建时的明确目标，路由失效不得猜测其他会话，跨租户扫描和崩溃恢复必须有界且可前进。

- [RULE-004] 扇出前必须在父任务行锁内同时确认 `status=RUNNING`、`cancel_requested=false`、`lease_until > now`、`lease_owner` 与本执行一致；已取消或已失约的父任务不得创建可执行 Child（`_require_live_owner`）。

- [RULE-005] fan-in 只对 BATCH 父执行生效；请求取消的父任务必须由取消路径收尾为 `CANCELLED`，不得被扇入改写成成功或失败；Agentic ASYNC Skill 子 Task 不是 BATCH 子任务，父 Execution 必须继续等待 operation 结果。

## Conventions

- **Run 来源的新 operation 冻结单次执行预算**：`TaskService._creation_values` 对有 `runtime_operation` 的 Task 使用 max_attempts=1，Child 继承；JOIN 从冻结 execution snapshot budget 读取带时区 run_deadline_at，并与自己的 Task deadline 取较早值，DETACH 受理后使用独立 Task deadline。立即提交每个真实 call 独立 operation，传输重试使用 `runtime-op:{operation_id}:submit/cancel`；受理响应包含可信 run/actor/operation/call/hash 与真实首次 Task 状态。✅ 重投同 operation，结果先到也不回退状态；❌ 把同 Run、Skill、输入的两次明确调用合并，或把提交未确认伪称 Task FAILED。

- **Parent-Child 批量幂等**：Parent 在**单事务**内创建 Child（带 `root_id`/`parent_id`/`item_key`），Child 幂等键为 `parent:{parent_id}:{item_key}`；partial unique `(parent_id, item_key) WHERE parent_id IS NOT NULL AND is_deleted = false` 保证同一 Child 只创建一次。reclaim 后已有 Child 只等待 fan-in，**不重复 fan-out**。（`application/batch_fanout.py`、`batch_fanin.py`）**`item_key` 只由严格 JSON 派生**（`muad_contracts.canonical`，2026-10-07）：显式 `item_key` 优先，否则规范化 JSON 的 sha256 前缀。❌ 用 `default=str` 兜底——`date(2026,1,1)` 与 `"2026-01-01"` 会撞成同一个键，partial unique 把第二条 Child **静默合并掉**（少跑一条且不报错）；键算不出来一律 `BATCH_PLAN_INVALID`（确定性失败）。
- **来源 operation 与父子事务使用同一锁序**：`application/terminal_tasks.py::lock_task_tree` 先锁可信 `runtime_operation`，再锁 root/Parent，最后更新 Child；无 operation 的既有任务沿用 Parent→Child。取消、终态、fan-in、deadline 和 reclaim 均遵守；扫描有界，每个根任务树独立提交，不跨无关 operation 保留行锁。✅ operation→root→Parent→Child→TaskEvent/outbox；❌ 先 UPDATE Child，或拿 root 锁后反向拿 operation 锁。
- **扇出前必须证明「本次执行仍持有父任务」**：父行锁内四格一起看——`status=RUNNING`、`cancel_requested=false`、`lease_until > now`、`lease_owner` 与本执行一致；已取消/已失约的父任务**不得**建出可执行的 Child（`application/batch_fanout.py` 的 `_require_live_owner`）。✅ `_require_live_owner(locked, claimed, moment)`；❌ 只按 `id` 加锁就插 Child。
- **fan-in 只对 BATCH 父执行生效**：`reclaim` 会把崩溃的父任务置回 `QUEUED`，此时最后一个 Child 终态若不肯聚合，父任务再没有下一个事件可触发 fan-in，只能停到 deadline；已请求取消的父任务除外（那要由它自己的取消路径收尾成 `CANCELLED`，不能被扇入改写成成功/失败）（`application/batch_fanin.py`）。Agentic 的 ASYNC Skill 子 Task 不是 BATCH 子任务，父 Execution 必须继续等待 operation 结果，不能被聚合为终态。
- **JOIN 根终态只经一个原子边界发布**：`application/terminal_tasks.py::write_terminal` 在调用方事务中完成状态/租约 CAS、TaskEvent 和 `runtime_result_outbox`；先 flush 事件满足复合 FK，再写发件，仍由同一事务提交/回滚。成功、失败、取消、deadline、崩溃耗尽及 BATCH fan-in 共用；BATCH Child 不发根结果，Task-owned ASYNC Skill 子 Task 终态经 `runtime_result_outbox` 发 `TaskToolOperationOwner` 到父 Execution 的操作收件箱（不是根结果）；DETACH 保留现有 final delivery。event_id 由 tenant/task/终态 seq 稳定派生。✅ 三者同事务，回滚三者都不存在；❌ 提交终态后再另起事务补结果发件。
- **Task-owned ASYNC Skill 子 Task 是持久执行载体**：提交 operation 后创建真实 Skill 子 Task（`parent_id=Agentic Execution`、`source_operation_id=operation_id`、`completion_mode=JOIN`、稳定幂等键），由正常 claim/lease 执行；子 Task 终态经 `runtime_result_outbox` 回写 Task owner 操作收件箱；父 Execution 保持 `WAITING_EXTERNAL`，由补扫续接。✅ 崩溃后子 Task 可重领、operation 不重复创建；❌ 只写 operation 不执行、直接跨 schema 写 `runtime.*`、或把父 Execution 当 BATCH 聚合。
- **取消的状态判断与写入必须在同一 Task 行锁内**：`TaskService.cancel` 按上述锁序取锁后 refresh；`QUEUED/WAITING` 通过共享终态函数写 CANCELLED、取消标记、事件与结果发件，`RUNNING` 保留状态并持久化 cancel_requested，由 Worker 协作停止。✅ 锁内复检再写取消意图，claim 赢了也留下 RUNNING/true；❌ 无锁读 QUEUED 后挑 CAS 分支，claim 抢跑导致两条 CAS 都不匹配并丢掉取消。
- **结果确认必须有 durable 证据**：`results/dispatcher.py` 发送前在 PG 预留 attempts/lease 并提交，网络在事务外；只有同 event_id 且 persisted=true 才写 SENT，旧租约不能确认。超时/5xx/非法回执退避，确定性 4xx 或耗尽保留 FAILED 并告警；有界前进游标保证其他租户能被扫描。启动参数从 SharedSettings 注入（默认 timeout=5、lease=30、poll=1、batch=32、base=1、cap=30、attempts=20）。✅ durable duplicate 同样确认；❌ 仅凭 HTTP 2xx 或 Redis hint 清掉发件。
- **取消可以先于 Task 到达**：可信来源在同 operation 行锁内写 cancellation tombstone，submission_hash 可为空；后续提交复核 tenant/run/call/actor 和指纹，已取消则 TASK_OPERATION_CANCELLED，不建 Task。FAILED 结果发件通过内部 admin retry 入口恢复，保持原 event_id/payload，并在 TaskEvent 留下操作者与原因。✅ 同来源取消重放、保留原结果重投；❌ 跳过 tombstone 创建 Task，或为修复投递重建业务任务。
- **Final Delivery 与 Human 问题共表、按 kind/稳定键去重**：`task_delivery` 是投递唯一状态源，`delivery_kind` 仅 `PUSH` / `HUMAN_QUESTION`；最终结果键为 `execution:{execution_id}:final`，Human 问题键为 `human:{interaction_id}`，同一键重放不新增。**可投递状态只有 `PENDING`**，`FAILED` 是终态（4xx / 路由缺失 / 次数耗尽）：可重试失败写回 `PENDING`，否则「还在退避中」和「已经没救了」在库里长得一样，耗尽的租户会永远占着跨租户扫描名额（`delivery/service.py` 的 `_query_pending_tenants` 与可重试失败分支）。**HTTP 2xx 不等于送达**：只有封套里**明确** `data.delivered == true` 才置 `SENT`，解析不了按可重试失败退避重投（`delivery/client.py` 的 ack 解析）。✅ `{"accepted": true, "delivered": true}` ⇒ SENT；❌ 非法 JSON / 缺 `delivered` 字段也当成功（结果再也不会重投，用户就是收不到）。
- **投递队列跨租户，必须公平且互不连坐**：按 `(最早待投递记录的 create_time, tenant_id)` 的**前进游标**逐租户取快照（`delivery/service.py` 的 `_query_pending_tenants`），单租户取快照失败只跳过它自己；预留（发送前自增 `delivery_attempts` 并提交）超过 `DELIVERY_RESERVATION_LEASE_SEC` 仍未回执的，按「那次尝试随进程没了」结算成 `FAILED`，不留永远无法再被领取的 `PENDING`（`delivery/service.py` 的预留结算路径）。✅ 固定前缀之外的租户每轮都会被看到；❌ 取「最早的前 N 个」且失败即抛——前 N 个选不出候选或读不到设置时，后面的租户永远轮不到。
- **回收同样受重试预算与退避约束**：`attempt >= max_attempts` 的崩溃任务终态 `FAILED(TASK_ATTEMPTS_EXHAUSTED)`，其余置回 `QUEUED` 并退避 `RETRY_BACKOFF_BASE_SEC * 2**(attempt-1)`（`worker/service.py` 的 reclaim 与退避路径）。
- **指标归属见 `harness-arch.md`**：回收指标归 Runtime，Worker 对应指标见各自 catalog；带 label 的计数器只进 `/metrics`，不写结构化 metric 日志。
- **指标是双出口，且仍有两枚计数器未进 `CATALOG`**：Worker 的计数既写结构化 `metric` 日志（`metric/amount/total`），也经 `inc_counter` 进 `/metrics`；无 label 的计数走 `increment()`，两出口同时写（`apps/agent-worker/src/muad_agent_worker/metrics.py`）。仍有个别计数器**不在 `CATALOG`**，因而无流量时不会出现在 `/metrics` 目录中（具体清单以 `metrics.py` 为准）。✅ 无 label 计数用 `increment()`（如需零流量可见须先登记进 `CATALOG`）；❌ 假设所有计数器都能在 `/metrics` 里凭空看到。
- **claim 不止 Task**：Delivery 与 Scheduler 各自持有 `FOR UPDATE SKIP LOCKED` 的取候选路径（`delivery/service.py`、`scheduler/service.py`），Scheduler 在事务内还会按 `revision`/`next_fire_at` 复检一次 CAS，暂停/删除若已赢得竞态则不返回行。投递退避为 `delivery_backoff_base_sec`（设置项，**生产默认 5**）× `2**delivery_attempts`，且 `delivery_attempts` 在发起请求前自增并提交，崩溃重启不丢退避进度（`delivery/service.py`）。
- **租约守卫式 CAS**：写任务状态的条件必须同时含 `lease_owner = 本实例` 与 `lease_until > now`——租约失效即失约，避免卡住后恢复的 Worker 用过期结果覆盖别人的执行（`worker/service.py` 的终态 CAS）；Schedule 侧口径为「状态判断与写入在同一行锁内完成」（`scheduler/service.py`）。**续租同口径**（`worker/service.py` 的心跳路径）：只认**当前租约仍有效**，否则卡顿超过租期后一次心跳就能把失效的执行权续到未来。✅ 续租条件含 `lease_until > now`；❌ 只看 `status`/`lease_owner` 就续租。
- **执行与租约维护共同受监督**：`run_once` 等的是「执行结束」与「心跳退出」中的先者，心跳先退出（续租连续失败或失约）即叫停本地执行（`worker/service.py` 的 `_supervise_task`）。续租的瞬时 DB 错误有界重试，重试用尽就退出心跳；**绝不**让执行器脱离租约监控——终态 CAS 拦得住写库，拦不住已经发生的外部副作用。
- **Skill 结果按协议强校验**：`result` / `wait` / `wait.external_ref` / `wait.not_before` 形状不对即 `TaskExecutionError(SKILL_RESULT_INVALID)`（**确定性**失败，不消耗重试预算），并且解析本身处在 `run_once` 的统一异常处理内——否则类型错会冒泡出去，任务停在没有心跳的 `RUNNING` 等回收、然后重跑同一个确定性错误（`worker/execution_outcomes.py`）。
- **deadline sweep 有独立循环**：Scheduler 的到期触发与 deadline sweep 是两个互相独立的受监督循环（`scheduler/service.py` 的 `asyncio.gather(fire_forever, sweep_forever)`），一轮慢的 Schedule 解析不得把 sweep 推后整轮——`task_deadline_sweep_interval_sec` 必须是实际最大延迟。✅ `asyncio.gather(fire_forever, sweep_forever)`；❌ 串在同一个 `while` 里先 `await run_due()` 再 sweep。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。

## Applied Deltas

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-007#DELTA-001","op":"add","rule":"RULE-001","payload_sha256":"50c8f5cd4fb9ebaa495fc0e4e9447c3bfb9a0f604cc82d1e083026ea99d1e117","at":"2026-11-16T05:09:15.751Z"}

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-019#DELTA-001","op":"update","rule":"RULE-worker-001","payload_sha256":"574a4f4af9aa84a23f9c261e9611835466ed2fea6c4cd9ad30de19351bc87cc3","at":"2026-11-16T05:09:15.751Z"}

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-021#DELTA-001","op":"add","rule":"RULE-002","payload_sha256":"1973d218b1f6dae1aca038d4aba9d809cef8c8a475dabd19bb4d7a8634e51cff","at":"2026-11-16T05:09:15.751Z"}

- {"source":"2026-11-15/scheduled-task-v2/scheduled-task-v2.md#TASK-022#DELTA-001","op":"add","rule":"RULE-003","payload_sha256":"479c01d3baf5396ab24664d74c31cd4bcb9aed1f2182d97904bfe96bde47c4ff","at":"2026-11-16T05:09:15.751Z"}

- {"source":"2026-11-17/async-skill-background/async-skill-background.md#TASK-003#DELTA-001","op":"update","rule":"RULE-001","payload_sha256":"9a4de02fbd835c4d954fb307e1f1cef91f51f6ef0daafe2197fe65416aadd154","at":"2026-11-18T11:48:11.022Z"}

- {"source":"2026-11-18/spec-rule-promotion/spec-rule-promotion.md#TASK-001#DELTA-009","op":"add","rule":"RULE-004","payload_sha256":"236a93a832a41276e8cdba358a5b28fcc07163604e5814334681a37138a5c671","at":"2026-11-18T14:06:44.262Z"}

- {"source":"2026-11-18/spec-rule-promotion/spec-rule-promotion.md#TASK-001#DELTA-010","op":"add","rule":"RULE-005","payload_sha256":"5cb69af33b6568e148ffea44c7b095941e5888c8b7b0d3def0948b29cb58c2eb","at":"2026-11-18T14:06:44.262Z"}
