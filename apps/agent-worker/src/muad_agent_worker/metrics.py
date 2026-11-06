"""Worker 指标目录（design 11-audit-observability §3.5 / docs/09 §6.3）。

两个出口共用同一进程内计数：

- 结构化 `metric` 日志（V1 既有链路，`metric/amount/total`，跨进程聚合由部署层按日志求和）；
- api-kit 进程内注册表 → 真实 HTTP `GET /metrics`（Prometheus 文本，供 OTel/监控抓取）。

指标不进入业务 Console，也不作为业务审计事实源（权威数据在 PostgreSQL）。
label 只含任务类型/状态、技能键、投递状态与维护节拍结局；不得包含 Secret、凭据、消息正文或资源 ID（UUID）。
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from typing import Final, Literal

from fastapi import FastAPI
from muad_api import declare_metric, inc_counter, install_metrics, set_gauge

logger = logging.getLogger("muad_agent_worker.metrics")

_counters: Counter[str] = Counter()

TASKS_METRIC: Final = "tasks_total"
TASK_QUEUE_DEPTH_METRIC: Final = "task_queue_depth"
TASK_RECLAIM_METRIC: Final = "task_reclaim_total"
TASK_LEASE_EXPIRED_METRIC: Final = "task_lease_expired_total"
#: 维护（回收）节拍结局：`ok` / `failed`——单次失败不静默，也不杀死独立维护循环（TASK-095）。
MAINTENANCE_METRIC: Final = "worker_maintenance_total"
SCHEDULED_FIRE_METRIC: Final = "scheduled_fire_total"
SCHEDULED_MISFIRE_METRIC: Final = "scheduled_misfire_total"
DELIVERY_METRIC: Final = "delivery_total"
#: 投递细粒度计数归口到本目录（此前散在 `delivery/service.py`，目录里看不到，无流量不暴露）。
DELIVERY_ATTEMPT_METRIC: Final = "delivery_attempt_total"
DELIVERY_FAILED_METRIC: Final = "delivery_failed_total"
#: 产物引用解析不到（404）：退文本形态的计数——**看得见**才不会被当成"本来就没产物"。
DELIVERY_ARTIFACT_GONE_METRIC: Final = "delivery_artifact_gone_total"
#: Skill 沙箱执行结局：与 Runtime 同口径（source/status），Worker 侧单独目录可见。
SKILL_EXECUTION_METRIC: Final = "skill_execution_total"
SANDBOX_OUTPUT_LIMIT_METRIC: Final = "sandbox_output_limit_total"
SANDBOX_TIMEOUT_METRIC: Final = "sandbox_timeout_total"
#: 取平台设置快照的失败计数在**调用方**侧（Console 端点不可达时 Console 收不到请求）。
PLATFORM_SETTINGS_FETCH_METRIC: Final = "platform_settings_fetch_total"
#: workspace 维护循环结局（TASK-013 指标目录；TASK-006 清理实现消费）。
WORKSPACE_CLEANUP_METRIC: Final = "workspace_cleanup_total"
#: workspace 配额拒绝（无 label：拒绝本身就是唯一维度，资源 ID 不进 label）。
WORKSPACE_QUOTA_REJECTED_METRIC: Final = "workspace_quota_rejected_total"

#: 清理状态（封闭枚举）；SCANNED 记扫描批次，DELETED/SKIPPED/FAILED 记逐目录结局。
WorkspaceCleanupStatus = Literal["SCANNED", "DELETED", "SKIPPED", "FAILED"]

COUNTER: Final = "counter"
GAUGE: Final = "gauge"

# 目录：(指标名, 类型, label 名, help)。安装时声明，使 `/metrics` 无流量时也暴露完整目录。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    ("runtime_result_dispatch_total", COUNTER, ("status",), "Durable result dispatch outcomes"),
    ("task_hint_degraded_total", COUNTER, ("status",), "Redis task hint degradations"),
    (TASKS_METRIC, COUNTER, ("type", "status"), "Task executions by task type and terminal status"),
    (TASK_QUEUE_DEPTH_METRIC, GAUGE, (), "Claimable tasks observed at the last claim poll"),
    (TASK_RECLAIM_METRIC, COUNTER, (), "Tasks requeued after their lease expired"),
    (TASK_LEASE_EXPIRED_METRIC, COUNTER, (), "Expired leases observed by the reclaimer"),
    (SCHEDULED_FIRE_METRIC, COUNTER, ("status",), "Schedule fires by outcome"),
    (SCHEDULED_MISFIRE_METRIC, COUNTER, (), "Schedules skipped for missing their fire time"),
    (DELIVERY_METRIC, COUNTER, ("status",), "Deliveries by resulting delivery status"),
    (DELIVERY_ATTEMPT_METRIC, COUNTER, (), "Delivery attempts started (reserve plus send)"),
    (DELIVERY_FAILED_METRIC, COUNTER, (), "Deliveries failed terminally or abandoned"),
    (
        DELIVERY_ARTIFACT_GONE_METRIC,
        COUNTER,
        (),
        "Deliveries downgraded because the artifact reference is gone",
    ),
    (MAINTENANCE_METRIC, COUNTER, ("status",), "Worker maintenance (reclaim) ticks by outcome (ok/failed)"),
    (
        PLATFORM_SETTINGS_FETCH_METRIC,
        COUNTER,
        ("caller", "result"),
        "Platform settings snapshot fetches by caller and result (ok/failed)",
    ),
    (
        SKILL_EXECUTION_METRIC,
        COUNTER,
        ("source", "status"),
        "Skill sandbox executions by skill source and outcome",
    ),
    (
        SANDBOX_OUTPUT_LIMIT_METRIC,
        COUNTER,
        ("source",),
        "Skill executions terminated or rejected by an output limit",
    ),
    (
        SANDBOX_TIMEOUT_METRIC,
        COUNTER,
        ("source",),
        "Skill executions terminated by the sandbox timeout",
    ),
    (
        WORKSPACE_CLEANUP_METRIC,
        COUNTER,
        ("status",),
        "Workspace maintenance outcomes by status (scanned/deleted/skipped/failed)",
    ),
    (
        WORKSPACE_QUOTA_REJECTED_METRIC,
        COUNTER,
        (),
        "Workspace acquisitions rejected by quota",
    ),
)


def increment(name: str, amount: int = 1) -> None:
    if amount <= 0:
        raise ValueError("metric increment must be positive")
    _counters[name] += amount
    logger.info(
        "metric",
        extra={"metric": name, "amount": amount, "total": _counters[name]},
    )
    inc_counter(name, amount)


def value(name: str) -> int:
    return _counters[name]


def record_outcome(name: str, status: str, labels: Mapping[str, str] | None = None) -> None:
    """按 `{status}` 记一次带 label 的目录计数（label 只进 `/metrics` 注册表）。"""
    inc_counter(name, 1, {**(labels or {}), "status": status})


def record_gauge(name: str, amount: float, labels: Mapping[str, str] | None = None) -> None:
    set_gauge(name, amount, labels)


def record_workspace_cleanup(status: WorkspaceCleanupStatus, amount: int = 1) -> None:
    """记一次 workspace 维护结局；label 只取封闭状态，不含路径/租户/资源 ID。"""
    if amount < 0:
        raise ValueError("workspace cleanup metric amount must not be negative")
    if amount == 0:
        return
    inc_counter(WORKSPACE_CLEANUP_METRIC, amount, {"status": status})


def record_workspace_quota_rejected() -> None:
    """记一次配额拒绝：拒绝必须可见，不能静默截断/跳过。"""
    inc_counter(WORKSPACE_QUOTA_REJECTED_METRIC, 1)


def install_worker_metrics(app: FastAPI) -> None:
    """声明目录并注册真实 HTTP `GET /metrics`（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
