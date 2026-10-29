"""Runtime 指标目录（design 11-audit-observability §3.5 / docs/09 §6.2）。

只导出 api-kit 的进程内注册表 → 真实 HTTP `GET /metrics`（Prometheus 文本，供 OTel/监控抓取）；
指标不进入业务 Console 渲染，也不作为业务审计事实源（权威数据在 PostgreSQL 的审计表）。
label 只含 Agent/Skill 键、工具名、provider/model 标识与状态码；不得包含 Secret、凭据、
消息正文或资源 ID（UUID）。

本任务（TASK-098）把此前各任务只留结构化日志的观测点归口到目录，家族互相可区分：

- **模型容量**：`model_capacity_total{workload,status}` 预占裁决、`model_settlement_total{state}`
  结算结局（`UNKNOWN` 保留占用与 `SETTLED` 一样可见）、`model_lease_lost_total{operation}`
  失约停手、`model_capacity_sweep_total` 过期清扫批量；
- **准入与队列**：`run_admission_total{status}` 受理/拒绝、`run_admission_counter_floor_total`
  计数行缺失降级、`run_queue_timeout_total` 排队期限到期、`run_stage_projection_total{stage,status}`
  排队/执行阶段投影结局；
- **执行槽**：`runtime_execution_slots` 配置值、`execution_slot_reclaim_total` 槽回收、
  `runtime_maintenance_total{loop,status}` 每个独立维护循环的节拍结局；
- **事件流**：`event_stream_hint_total{status}` hint 降级/恢复、`event_tail_hub_total{outcome}`
  共享追尾（唤醒/补查/游标分型/重建）、`reply_delta_total{outcome}` 旧尝试围栏、
  `tool_call_audit_total{outcome}` 审计幂等重放；
- **交付与清理**：`channel_delivery_total{status}` 诚实结局（含 `UNKNOWN`）、
  `channel_delivery_failure_total{reason}`、`webchat_retention_total{status}` 与
  `webchat_retention_removed_total{kind}` 四类回收量。

label 取值一律是封闭字面量（本模块的 Literal 别名）或目录错误码，**不含** run/conversation/
notification UUID、投递键、正文与凭据。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from fastapi import FastAPI
from muad_api import declare_metric, inc_counter, install_metrics, set_gauge

AGENT_RUNS_METRIC: Final = "agent_runs_total"
MODEL_INVOCATIONS_METRIC: Final = "model_invocations_total"
TOOL_CALLS_METRIC: Final = "tool_calls_total"
SKILL_LOAD_METRIC: Final = "skill_load_total"
# 被目录上限丢掉的技能条数记在 **amount**（同 memory 注入/摘要 token 的口径：条数进 label 会
# 裂出无界时间序列）。它不是"某条技能失败"，而是"这一轮有多少技能没能进提示词"。
SKILL_CATALOG_TRUNCATED_METRIC: Final = "skill_catalog_truncated_total"
EGRESS_CALLS_METRIC: Final = "egress_calls_total"
ARTIFACT_BYTES_METRIC: Final = "artifact_bytes_total"
RUN_RECLAIM_METRIC: Final = "run_reclaim_total"
MEMORY_WRITE_METRIC: Final = "memory_write_total"
MEMORY_INJECT_METRIC: Final = "memory_inject_total"
MEMORY_INJECT_DROPPED_METRIC: Final = "memory_inject_dropped_total"
MEMORY_RECALL_METRIC: Final = "memory_recall_total"
MEMORY_RECALL_HIT_METRIC: Final = "memory_recall_hit_total"
MEMORY_RECALL_BYTES_METRIC: Final = "memory_recall_bytes_total"
MEMORY_RESUME_METRIC: Final = "memory_resume_total"
MEMORY_CONFIRMATION_METRIC: Final = "memory_confirmation_total"
MEMORY_CONSOLIDATION_ENQUEUE_METRIC: Final = "memory_consolidation_enqueue_total"
MEMORY_CONSOLIDATION_METRIC: Final = "memory_consolidation_total"
MEMORY_QUOTA_EVICTIONS_METRIC: Final = "memory_quota_evictions_total"
MEMORY_EXPIRY_METRIC: Final = "memory_expiry_total"
MEMORY_WRITE_CONFLICTS_METRIC: Final = "memory_write_conflicts_total"
CONTEXT_COMPACTION_METRIC: Final = "context_compaction_total"
CONTEXT_COMPACTION_BYTES_SAVED_METRIC: Final = "context_compaction_bytes_saved_total"
CONTEXT_SUMMARY_METRIC: Final = "context_summary_total"
CONTEXT_SUMMARY_TOKENS_METRIC: Final = "context_summary_tokens_total"
# 取平台设置快照的失败计数在**调用方**侧（Console 端点不可达时 Console 收不到请求）。
PLATFORM_SETTINGS_FETCH_METRIC: Final = "platform_settings_fetch_total"

# --- 模型调用与容量（TASK-022/023；TASK-098 归口） ---------------------------------------
#: 预占裁决按 workload（INTERACTIVE/BACKGROUND/PROBE）与结局计数：拒绝与放行都可区分。
MODEL_CAPACITY_METRIC: Final = "model_capacity_total"
#: 结算/释放/保留的**结果状态**：`SETTLED` 与 `UNKNOWN`（保留占用）是不同样本。
MODEL_SETTLEMENT_METRIC: Final = "model_settlement_total"
#: 失约（epoch/owner/期限不符）停手：执行方必须停手，不能只靠一次心跳续到未来。
MODEL_LEASE_LOST_METRIC: Final = "model_lease_lost_total"
#: 过期 ACTIVE 租约的单轮清扫批量记在 **amount**（条数进 label 会裂出无界时间序列）。
MODEL_SWEEP_METRIC: Final = "model_capacity_sweep_total"

# --- 准入、排队与执行槽（TASK-029/030/031/032/036） ---------------------------------------
RUN_ADMISSION_METRIC: Final = "run_admission_total"
#: 计数行缺失（投影与 Run 事实不一致）按 0 截断：降级可见，不静默。
RUN_ADMISSION_FLOOR_METRIC: Final = "run_admission_counter_floor_total"
#: 初始排队期限到期：超时后不再调用模型（此前只有 `run_queue_timeout` 告警日志）。
RUN_QUEUE_TIMEOUT_METRIC: Final = "run_queue_timeout_total"
#: 排队/执行阶段投影（WAITING_MODEL/RUNNING）结局：投影失败不得静默降级。
RUN_STAGE_PROJECTION_METRIC: Final = "run_stage_projection_total"
#: 启动时配置的执行槽数（typed 设置的真实取值，无流量也可见）。
EXECUTION_SLOTS_METRIC: Final = "runtime_execution_slots"
#: 失去归属的本地执行片段被回收的次数（槽位账本不漂移）。
EXECUTION_SLOT_RECLAIM_METRIC: Final = "execution_slot_reclaim_total"
# --- 队列容量（TASK-005 S-13 / B-01；HPA 的自定义指标来源） -------------------------------
#: 按排队阶段（WAITING_RUNTIME/WAITING_MODEL）的在飞队列深度。
QUEUE_DEPTH_METRIC: Final = "runtime_queue_depth"
#: 最老排队 Run 的等待秒数（PG 服务端时钟）；HPA 用它按等待时长扩容。
OLDEST_QUEUE_AGE_METRIC: Final = "runtime_oldest_queue_age_seconds"
#: 已离开队列、正在执行的 Run 数。
EXECUTION_INFLIGHT_METRIC: Final = "runtime_execution_inflight"
#: 本租户非终态 Run 总数（与队列深度区分：执行中 + 排队）。
OPEN_RUNS_METRIC: Final = "runtime_open_runs"
#: memory consolidation 队列：PENDING/RUNNING/RETRY 三个状态。
MEMORY_JOB_QUEUE_METRIC: Final = "runtime_memory_job_queue"
#: 本进程准入闸门：在飞 / 排队等待数（B-01 有界拒绝的观测面）。
ADMISSION_INFLIGHT_METRIC: Final = "runtime_admission_inflight"
ADMISSION_WAITING_METRIC: Final = "runtime_admission_waiting"
#: 每个**独立**维护循环的节拍结局：单次失败留痕且不杀循环（design §3.2）。
RUNTIME_MAINTENANCE_METRIC: Final = "runtime_maintenance_total"

# --- 事件流（TASK-060/062） ---------------------------------------------------------------
#: Redis hint 只影响唤醒时延：降级与恢复都要可见（PG 补查兜底不等于没降级）。
EVENT_STREAM_HINT_METRIC: Final = "event_stream_hint_total"
#: 共享追尾的关键路径（唤醒/补查/游标分型/窗口重建）：慢消费者重建不静默。
EVENT_TAIL_HUB_METRIC: Final = "event_tail_hub_total"
#: 回复增量：真实产出与旧尝试迟到输出被围栏是不同样本（design §3.6）。
REPLY_DELTA_METRIC: Final = "reply_delta_total"
#: 工具审计幂等：写入与重放跳过（保留原行）都可对账。
TOOL_AUDIT_METRIC: Final = "tool_call_audit_total"

# --- 交付与清理（TASK-066/069） -----------------------------------------------------------
#: 交付结局按**实际发送结果**分档：`DELIVERED`/`NOT_SENT`/`UNKNOWN` 不互相冒充。
CHANNEL_DELIVERY_METRIC: Final = "channel_delivery_total"
#: 泵失败/尝试失败/单条崩溃是三种不同的失败面。
CHANNEL_DELIVERY_FAILURE_METRIC: Final = "channel_delivery_failure_total"
#: 保留清理节拍结局：清理失败不静默，也不杀死独立维护循环。
WEBCHAT_RETENTION_METRIC: Final = "webchat_retention_total"
#: 四类清理各自的回收量记在 **amount**（条数进 label 会裂出无界时间序列）。
WEBCHAT_RETENTION_REMOVED_METRIC: Final = "webchat_retention_removed_total"

# --- 诊断分段计时（1000 用户容量瓶颈定位） -----------------------------------------------
#: 从连接池取连接的等待时长。SQLAlchemy 公开的 `checkout` 事件在连接**已取到**之后才触发，
#: 排队时长只能在池的取连接处测（`db_timing.TimedAsyncAdaptedQueuePool`）。
DB_POOL_CHECKOUT_SECONDS: Final = "db_pool_checkout_seconds"
#: 连接从 checkout 到 checkin 的持有时间（事务 + 事务间空闲都在内）。
DB_CONNECTION_HOLD_SECONDS: Final = "db_connection_hold_seconds"
#: SQLAlchemy 会话 commit 的墙钟（含 flush；用于拆分 select/capacity 的 commit 尾巴）。
DB_COMMIT_SECONDS: Final = "db_commit_seconds"
#: 每进程 SELECT 1 探针：池/连接/await 的纯往返成本（诊断，默认关闭）。
DB_PROBE_SECONDS: Final = "db_probe_seconds"
#: 事件循环 lag：固定间隔 sleep 的实际超时（诊断，默认关闭）。
EVENT_LOOP_LAG_SECONDS: Final = "event_loop_lag_seconds"
#: GC 停顿：start→stop 回调之间的墙钟（诊断，默认关闭）。
GC_PAUSE_SECONDS: Final = "gc_pause_seconds"
#: GC 触发次数（按代际；与停顿时长分开，便于区分高频短停顿与低频长停顿）。
GC_COLLECTIONS_TOTAL: Final = "gc_collections_total"
#: 单次 canonical 事件写入（`RunService._persist_event`，含批量等待 + 一次批 commit）的时长。
RUN_PERSIST_EVENT_SECONDS: Final = "run_persist_event_seconds"
#: 完整回合 checkpoint（`DurableRuntimeEventPort.checkpoint`）的时长。
RUN_CHECKPOINT_SECONDS: Final = "run_checkpoint_seconds"
#: 执行片段分解：`stage=first_event|total|persist|wait`（wait = total - persist，含模型与
#: 执行器内部工作）。四段相加不重复：first_event 是 total 的前缀样本。
RUN_CONSUME_SECONDS: Final = "run_consume_seconds"
#: 终态收尾（`_finalize_run`/`_finalize_failed`）的时长。
RUN_FINALIZE_SECONDS: Final = "run_finalize_seconds"
#: 终态事务分段：lock/cancel/run_cas/release/events/submission/notification/activity/memory/commit。
RUN_FINALIZE_STAGE_SECONDS: Final = "run_finalize_stage_seconds"
#: 事件批量化：flush 时长、从 submit 到 commit 的等待、最近一批大小、累计提交事件数。
EVENT_BATCH_FLUSH_SECONDS: Final = "event_batch_flush_seconds"
EVENT_BATCH_WAIT_SECONDS: Final = "event_batch_wait_seconds"
EVENT_BATCH_SIZE: Final = "event_batch_size"
EVENT_BATCH_EVENTS_TOTAL: Final = "event_batch_events_total"
#: batch flush 内部分段：锁 / PG 时钟 / seq / INSERT / COMMIT / 入队等待。
EVENT_BATCH_LOCK_SECONDS: Final = "event_batch_lock_seconds"
EVENT_BATCH_CONVERSATION_LOCK_SECONDS: Final = "event_batch_conversation_lock_seconds"
EVENT_BATCH_RUN_LOCK_SECONDS: Final = "event_batch_run_lock_seconds"
EVENT_BATCH_CONTINUATION_LOCK_SECONDS: Final = "event_batch_continuation_lock_seconds"
EVENT_BATCH_CLOCK_SECONDS: Final = "event_batch_clock_seconds"
EVENT_BATCH_SEQ_SECONDS: Final = "event_batch_seq_seconds"
EVENT_BATCH_INSERT_SECONDS: Final = "event_batch_insert_seconds"
EVENT_BATCH_COMMIT_SECONDS: Final = "event_batch_commit_seconds"
EVENT_BATCH_QUEUE_SECONDS: Final = "event_batch_queue_seconds"
EVENT_BATCH_RUNS: Final = "event_batch_runs"
EVENT_BATCH_CONVERSATIONS: Final = "event_batch_conversations"
EVENT_BATCH_CONVERSATIONS_TOTAL: Final = "event_batch_conversations_total"
EVENT_BATCH_RUNS_TOTAL: Final = "event_batch_runs_total"
EVENT_BATCH_FLUSHES_TOTAL: Final = "event_batch_flushes_total"
EVENT_BATCH_CONVERSATION_BUCKET: Final = "event_batch_conversation_bucket"
EVENT_BATCH_RUN_BUCKET: Final = "event_batch_run_bucket"
EVENT_BATCH_QUEUE_DEPTH: Final = "event_batch_queue_depth"
#: 执行租约续租：墙钟时长与失败/失约次数（与事件写分开观测）。
RUN_LEASE_RENEW_SECONDS: Final = "run_lease_renew_seconds"
RUN_LEASE_RENEW_FAILURES_TOTAL: Final = "run_lease_renew_failures_total"
#: 受理分段：`stage=gate|precheck|materials|claim|write`（write 不含 claim）。
RUN_ADMISSION_STAGE_SECONDS: Final = "run_admission_stage_seconds"
#: 受理写事务内部：first_stmt/policy/inserts/submission/attachments/memory/events/conversation/counts/commit。
RUN_ADMISSION_CREATE_STAGE_SECONDS: Final = "run_admission_create_stage_seconds"
#: 模型调用分段：`stage=candidates|reserve|credentials|upstream|total`（预留/凭据/上游分开）。
MODEL_CALL_SECONDS: Final = "model_call_seconds"

#: 时长桶上界（秒）。桶按累计语义记录：样本 `<=` 上界即计入该桶。
LATENCY_BUCKETS_SECONDS: Final = (
    0.0005,
    0.001,
    0.002,
    0.005,
    0.01,
    0.02,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    30.0,
)

#: 分段计时族：(族名, label 名, help)。每个族导出**累计桶** `<族名>_le_<上界>`（上界编在
#: 指标名里，如 `..._le_50ms`）+ `<族名>_sum` + `<族名>_count`。桶上界不进 label：label 名
#: 是跨单元的封闭白名单（`tests/architecture/...` 机检），把上界编进名字同样可枚举、可复算。
_LATENCY_FAMILIES: Final[tuple[tuple[str, tuple[str, ...], str], ...]] = (
    (DB_POOL_CHECKOUT_SECONDS, (), "Time spent waiting for a pooled database connection"),
    (DB_CONNECTION_HOLD_SECONDS, (), "Time a pooled database connection was checked out"),
    (DB_COMMIT_SECONDS, (), "SQLAlchemy session commit duration (incl. flush)"),
    (DB_PROBE_SECONDS, (), "Per-process SELECT 1 probe latency"),
    (EVENT_LOOP_LAG_SECONDS, (), "Event loop lag measured as sleep overshoot"),
    (GC_PAUSE_SECONDS, (), "Stop-the-world GC pause duration"),
    (RUN_PERSIST_EVENT_SECONDS, (), "Canonical event persistence duration per event"),
    (RUN_CHECKPOINT_SECONDS, (), "Complete-round checkpoint persistence duration"),
    (
        RUN_CONSUME_SECONDS,
        ("stage",),
        "Run executor consumption split by stage (first_event/total/persist/wait)",
    ),
    (RUN_FINALIZE_SECONDS, (), "Run terminal finalization duration"),
    (
        RUN_FINALIZE_STAGE_SECONDS,
        ("stage",),
        "Run terminal finalization split by stage",
    ),
    (
        EVENT_BATCH_FLUSH_SECONDS,
        (),
        "Event batcher flush duration (lock + insert + commit)",
    ),
    (
        EVENT_BATCH_WAIT_SECONDS,
        (),
        "Event batcher time from submit to committed",
    ),
    (EVENT_BATCH_LOCK_SECONDS, (), "Event batcher source lock wait (total)"),
    (EVENT_BATCH_CONVERSATION_LOCK_SECONDS, (), "Event batcher conversation row lock wait"),
    (EVENT_BATCH_RUN_LOCK_SECONDS, (), "Event batcher run row lock wait"),
    (EVENT_BATCH_CONTINUATION_LOCK_SECONDS, (), "Event batcher continuation row lock wait"),
    (EVENT_BATCH_CLOCK_SECONDS, (), "Event batcher database clock wait"),
    (EVENT_BATCH_SEQ_SECONDS, (), "Event batcher sequence allocation"),
    (EVENT_BATCH_INSERT_SECONDS, (), "Event batcher INSERT/flush"),
    (EVENT_BATCH_COMMIT_SECONDS, (), "Event batcher COMMIT"),
    (EVENT_BATCH_QUEUE_SECONDS, (), "Event batcher queue wait before flush"),
    (RUN_LEASE_RENEW_SECONDS, (), "Execution lease renew duration"),
    (
        RUN_ADMISSION_STAGE_SECONDS,
        ("stage",),
        "Run admission split by stage (gate/precheck/materials/claim/write)",
    ),
    (
        RUN_ADMISSION_CREATE_STAGE_SECONDS,
        ("stage",),
        "Run create transaction split by internal stage",
    ),
    (
        MODEL_CALL_SECONDS,
        ("stage",),
        "Model call split by stage (candidates/reserve/credentials/upstream/total)",
    ),
)

#: 共享追尾 outcome 的封闭词表（`event_tail_hub_total` 的 label 取值）。
TailHubOutcome = Literal[
    "wakeup",
    "fallback_poll",
    "fallback_failure",
    "lagged_rebuild",
    "cursor_ahead",
    "cursor_expired",
    "hint_failure",
]
#: 独立维护循环名的封闭词表（`runtime_maintenance_total` 的 label 取值）。
RuntimeLoop = Literal[
    "resume",
    "memory",
    "deadline",
    "control",
    "reaper",
    "execution_slot",
    "delivery",
    "retention",
]
#: 失约操作的封闭词表（`model_lease_lost_total` 的 label 取值）。
LeaseLostOperation = Literal["transition", "renew", "execute"]
#: 交付泵失败面的封闭词表（`channel_delivery_failure_total` 的 label 取值）。
DeliveryFailureReason = Literal["pump_failed", "attempt_failed", "crashed"]
#: 保留清理四类对象的封闭词表（`webchat_retention_removed_total` 的 label 取值）。
RetentionKind = Literal["stream_replay", "history", "notifications", "staging_upload"]

COUNTER: Final = "counter"
GAUGE: Final = "gauge"
HISTOGRAM: Final = "histogram"

#: 分段计时族在目录里按 histogram 声明（无流量也可见，`/metrics` 有 `# TYPE` 行）；线格式是
#: `<族名>_le_<上界>`/`_sum`/`_count` 三个 counter 序列 —— 落在既有 api-kit 注册表能力之内。
_LATENCY_CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = tuple(
    (name, HISTOGRAM, labels, help_text) for name, labels, help_text in _LATENCY_FAMILIES
)

# 目录：(指标名, 类型, label 名, help)。安装时声明，使 `/metrics` 无流量时也暴露完整目录。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    (AGENT_RUNS_METRIC, COUNTER, ("agent", "status"), "Agent runs by agent key and outcome"),
    (GC_COLLECTIONS_TOTAL, COUNTER, ("generation",), "GC collections by generation"),
    (EVENT_BATCH_SIZE, GAUGE, (), "Last event batch size (events per flush)"),
    (EVENT_BATCH_EVENTS_TOTAL, COUNTER, (), "Events committed through the event batcher"),
    (EVENT_BATCH_RUNS, GAUGE, (), "Runs in the last event batch"),
    (EVENT_BATCH_CONVERSATIONS, GAUGE, (), "Conversations in the last event batch"),
    (EVENT_BATCH_CONVERSATIONS_TOTAL, COUNTER, (), "Sum of conversations across event batches"),
    (EVENT_BATCH_RUNS_TOTAL, COUNTER, (), "Sum of runs across event batches"),
    (EVENT_BATCH_FLUSHES_TOTAL, COUNTER, (), "Event batch flush count"),
    (EVENT_BATCH_CONVERSATION_BUCKET, COUNTER, ("bucket",), "Batches by conversation count bucket"),
    (EVENT_BATCH_RUN_BUCKET, COUNTER, ("bucket",), "Batches by run count bucket"),
    (EVENT_BATCH_QUEUE_DEPTH, GAUGE, (), "Event batcher queue depth at flush start"),
    (RUN_LEASE_RENEW_FAILURES_TOTAL, COUNTER, (), "Execution lease renew failures/losses"),
    ("tool_control_dispatch_total", COUNTER, ("status",), "Durable tool control delivery outcomes"),
    (
        MODEL_INVOCATIONS_METRIC,
        COUNTER,
        ("provider", "model", "status"),
        "Model invocations by provider, model and status",
    ),
    (TOOL_CALLS_METRIC, COUNTER, ("kind", "tool", "status"), "Tool calls by kind, tool and status"),
    (SKILL_LOAD_METRIC, COUNTER, ("skill", "status"), "Skill package loads by skill key and status"),
    (
        SKILL_CATALOG_TRUNCATED_METRIC,
        COUNTER,
        (),
        "Skills dropped from the prompt catalog by the catalog budget",
    ),
    (EGRESS_CALLS_METRIC, COUNTER, ("platform", "status"), "Egress calls by platform and status"),
    (ARTIFACT_BYTES_METRIC, COUNTER, ("type",), "Artifact bytes written by artifact type"),
    (RUN_RECLAIM_METRIC, COUNTER, (), "Abandoned runs reaped back by the run reaper"),
    ("db_pool_checked_out", GAUGE, (), "Database connections currently checked out"),
    ("db_pool_size", GAUGE, (), "Open database connections held by the pool"),
    (
        MEMORY_WRITE_METRIC,
        COUNTER,
        ("outcome",),
        "Committed memory writes and rejected command attempts",
    ),
    # 注入/检索的"条数与字节"记在 **amount** 而不是 label：label 里放计数值会按每次取值
    # 裂出新的时间序列（无界基数），与本模块 docstring 的 label 卫生口径冲突。
    (MEMORY_INJECT_METRIC, COUNTER, ("outcome",), "Memory injection attempts by outcome"),
    (
        MEMORY_INJECT_DROPPED_METRIC,
        COUNTER,
        ("reason",),
        "Memory entries dropped by final count or byte budget",
    ),
    (MEMORY_RECALL_METRIC, COUNTER, ("mode", "outcome"), "Recall tool calls by mode and outcome"),
    (MEMORY_RECALL_HIT_METRIC, COUNTER, ("hit",), "Recall search hits; excludes LIST and zero limit"),
    (MEMORY_RECALL_BYTES_METRIC, COUNTER, (), "Bytes returned by the recall tool"),
    (MEMORY_RESUME_METRIC, COUNTER, ("outcome",), "Runtime task resume decisions by outcome"),
    (MEMORY_CONFIRMATION_METRIC, COUNTER, ("outcome",), "Memory proposal and confirmation outcomes"),
    (MEMORY_CONSOLIDATION_ENQUEUE_METRIC, COUNTER, ("outcome",), "Consolidation enqueue outcomes"),
    (MEMORY_CONSOLIDATION_METRIC, COUNTER, ("outcome",), "Consolidation claim and completion outcomes"),
    (MEMORY_QUOTA_EVICTIONS_METRIC, COUNTER, (), "Committed automatic memory quota evictions"),
    (MEMORY_EXPIRY_METRIC, COUNTER, ("outcome",), "Memory expiry sweep outcomes"),
    (MEMORY_WRITE_CONFLICTS_METRIC, COUNTER, ("reason",), "Memory command conflicts by bounded reason"),
    # 压缩四级计数器（design §3.5 可观测性）：label 只有层名与结果，不带资源 ID。
    (
        CONTEXT_COMPACTION_METRIC,
        COUNTER,
        ("layer", "status"),
        "Context compaction layers by layer and status",
    ),
    (
        CONTEXT_COMPACTION_BYTES_SAVED_METRIC,
        COUNTER,
        ("layer",),
        "Bytes saved by context compaction by layer",
    ),
    (CONTEXT_SUMMARY_METRIC, COUNTER, ("status",), "Summary layer runs by status"),
    # token 用量记在 **amount**，不进 label（label 里放计数值会裂出无界时间序列）。
    (CONTEXT_SUMMARY_TOKENS_METRIC, COUNTER, (), "Tokens spent on summary model calls"),
    (
        PLATFORM_SETTINGS_FETCH_METRIC,
        COUNTER,
        ("caller", "result"),
        "Platform settings snapshot fetches by caller and result (ok/failed)",
    ),
    # --- TASK-098：模型、准入、流与交付（此前只留日志的观测点） ---------------------------
    (
        MODEL_CAPACITY_METRIC,
        COUNTER,
        ("workload", "status"),
        "Model capacity reservations by workload and outcome (RESERVED/REUSED or error code)",
    ),
    (
        MODEL_SETTLEMENT_METRIC,
        COUNTER,
        ("status",),
        "Model call settlements by resulting lease state (SETTLED/UNKNOWN)",
    ),
    (
        MODEL_LEASE_LOST_METRIC,
        COUNTER,
        ("status",),
        "Model call lease losses by operation (transition/renew/execute)",
    ),
    (
        MODEL_SWEEP_METRIC,
        COUNTER,
        (),
        "Expired active model leases marked UNKNOWN by the bounded sweep",
    ),
    (
        RUN_ADMISSION_METRIC,
        COUNTER,
        ("status",),
        "Run admission outcomes (ACCEPTED or catalog error code)",
    ),
    (
        RUN_ADMISSION_FLOOR_METRIC,
        COUNTER,
        (),
        "Admission counter rows missing and truncated at zero",
    ),
    (RUN_QUEUE_TIMEOUT_METRIC, COUNTER, (), "Runs that expired while waiting for an execution slot"),
    (
        RUN_STAGE_PROJECTION_METRIC,
        COUNTER,
        ("stage", "status"),
        "Run queue/execution stage projections by stage and outcome (ok/failed)",
    ),
    (
        EXECUTION_SLOTS_METRIC,
        GAUGE,
        (),
        "Configured runtime execution slots at startup",
    ),
    (
        EXECUTION_SLOT_RECLAIM_METRIC,
        COUNTER,
        (),
        "Stale local executions reclaimed by the slot supervisor",
    ),
    (QUEUE_DEPTH_METRIC, GAUGE, ("stage",), "Runs waiting in each runtime queue stage"),
    (
        OLDEST_QUEUE_AGE_METRIC,
        GAUGE,
        ("stage",),
        "Oldest runtime queue wait in seconds by stage",
    ),
    (EXECUTION_INFLIGHT_METRIC, GAUGE, (), "Runs currently executing outside the queue"),
    (OPEN_RUNS_METRIC, GAUGE, (), "Non-terminal runs visible to this runtime tenant"),
    (
        MEMORY_JOB_QUEUE_METRIC,
        GAUGE,
        ("state",),
        "Memory consolidation jobs by state",
    ),
    (ADMISSION_INFLIGHT_METRIC, GAUGE, (), "Admissions currently holding the process gate"),
    (ADMISSION_WAITING_METRIC, GAUGE, (), "Admissions waiting for the process gate"),
    (
        RUNTIME_MAINTENANCE_METRIC,
        COUNTER,
        ("loop", "status"),
        "Independent runtime maintenance loops by loop and outcome (ok/failed)",
    ),
    (
        EVENT_STREAM_HINT_METRIC,
        COUNTER,
        ("status",),
        "Event stream wakeup hints by status (ok/degraded)",
    ),
    (
        EVENT_TAIL_HUB_METRIC,
        COUNTER,
        ("outcome",),
        "Shared event tail hub by bounded outcome (wakeup/fallback/cursor/rebuild)",
    ),
    (REPLY_DELTA_METRIC, COUNTER, ("status",), "Reply deltas by outcome (emitted/fenced)"),
    (
        TOOL_AUDIT_METRIC,
        COUNTER,
        ("status",),
        "Tool call audit writes by outcome (inserted/replay_skipped)",
    ),
    (
        CHANNEL_DELIVERY_METRIC,
        COUNTER,
        ("status",),
        "Channel deliveries by honest outcome (DELIVERED/NOT_SENT/UNKNOWN)",
    ),
    (
        CHANNEL_DELIVERY_FAILURE_METRIC,
        COUNTER,
        ("reason",),
        "Channel delivery pump failures by bounded reason",
    ),
    (
        WEBCHAT_RETENTION_METRIC,
        COUNTER,
        ("status",),
        "Webchat retention sweeps by outcome (ok/failed)",
    ),
    (
        WEBCHAT_RETENTION_REMOVED_METRIC,
        COUNTER,
        ("kind",),
        "Webchat retention rows/bytes removed by object kind",
    ),
) + _LATENCY_CATALOG


def _bound_suffix(bound: float) -> str:
    """桶上界 → 指标名后缀（只能含字母数字下划线：`500us`/`50ms`/`1s`）。"""
    if bound < 1.0:
        micros = round(bound * 1_000_000)
        return f"{micros // 1000}ms" if micros % 1000 == 0 else f"{micros}us"
    return f"{bound:g}s"


def record_latency(
    name: str, seconds: float, labels: Mapping[str, str] | None = None
) -> None:
    """记一次时长：累计桶 `<name>_le_<上界>` + `<name>_sum` + `<name>_count`。

    桶只在上界 `>=` 本次样本时累加（累计语义），因此任一枚举维度的分位数都可从桶差复算。
    时长按非负值截断：负样本只可能来自时钟回拨，不能让它污染桶。
    """
    value = max(0.0, float(seconds))
    base = dict(labels or {})
    for bound in LATENCY_BUCKETS_SECONDS:
        if value <= bound:
            inc_counter(f"{name}_le_{_bound_suffix(bound)}", 1, base)
    inc_counter(f"{name}_sum", value, base)
    inc_counter(f"{name}_count", 1, base)


def record_counter(name: str, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
    """累加一次计数（非结局语义的指标，如 artifact 字节数、清扫批量）。"""
    inc_counter(name, amount, labels)


def record_outcome(name: str, status: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{status}` 记一次结果；status 取审计表口径（`OK`/`FAILED`）或 catalog 错误码。"""
    inc_counter(name, 1, {**(labels or {}), "status": status})


def record_tail_outcome(outcome: TailHubOutcome) -> None:
    """记一次共享追尾的关键路径：outcome 只取封闭词表（唤醒/补查/游标分型/重建/降级）。"""
    inc_counter(EVENT_TAIL_HUB_METRIC, 1, {"outcome": outcome})


def record_reason(name: str, reason: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{reason}` 记一次带原因的结果：reason 只取封闭词表，不含资源 ID/凭据。"""
    inc_counter(name, 1, {**(labels or {}), "reason": reason})


def record_gauge(name: str, amount: float, labels: Mapping[str, str] | None = None) -> None:
    """发布一个瞬时值（如配置的执行槽数）：无流量时目录仍由 `declare_metric` 声明。"""
    set_gauge(name, amount, labels)


def install_runtime_metrics(app: FastAPI) -> None:
    """声明目录并注册真实 HTTP `GET /metrics`（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
