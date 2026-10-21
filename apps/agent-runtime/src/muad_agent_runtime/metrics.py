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

#: 共享追尾 outcome 的封闭词表（`event_tail_hub_total` 的 label 取值）。
TailHubOutcome = Literal[
    "wakeup",
    "fallback_poll",
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

# 目录：(指标名, 类型, label 名, help)。安装时声明，使 `/metrics` 无流量时也暴露完整目录。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    (AGENT_RUNS_METRIC, COUNTER, ("agent", "status"), "Agent runs by agent key and outcome"),
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
)


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
