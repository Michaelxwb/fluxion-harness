"""Worker 指标目录（design 11-audit-observability §3.5 / docs/09 §6.3）。

两个出口共用同一进程内计数：

- 结构化 `metric` 日志（V1 既有链路，`metric/amount/total`，跨进程聚合由部署层按日志求和）；
- api-kit 进程内注册表 → 真实 HTTP `GET /metrics`（Prometheus 文本，供 OTel/监控抓取）。

指标不进入业务 Console，也不作为业务审计事实源（权威数据在 PostgreSQL）。
label 只含任务类型/状态、技能键与投递状态；不得包含 Secret、凭据、消息正文或资源 ID（UUID）。
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from typing import Final

from fastapi import FastAPI
from muad_api import declare_metric, inc_counter, install_metrics, set_gauge

logger = logging.getLogger("muad_agent_worker.metrics")

_counters: Counter[str] = Counter()

TASKS_METRIC: Final = "tasks_total"
TASK_QUEUE_DEPTH_METRIC: Final = "task_queue_depth"
TASK_RECLAIM_METRIC: Final = "task_reclaim_total"
TASK_LEASE_EXPIRED_METRIC: Final = "task_lease_expired_total"
SCHEDULED_FIRE_METRIC: Final = "scheduled_fire_total"
SCHEDULED_MISFIRE_METRIC: Final = "scheduled_misfire_total"
DELIVERY_METRIC: Final = "delivery_total"

COUNTER: Final = "counter"
GAUGE: Final = "gauge"

# 目录：(指标名, 类型, label 名, help)。安装时声明，使 `/metrics` 无流量时也暴露完整目录。
CATALOG: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
    (TASKS_METRIC, COUNTER, ("type", "status"), "Task executions by task type and terminal status"),
    (TASK_QUEUE_DEPTH_METRIC, GAUGE, (), "Claimable tasks observed at the last claim poll"),
    (TASK_RECLAIM_METRIC, COUNTER, (), "Tasks requeued after their lease expired"),
    (TASK_LEASE_EXPIRED_METRIC, COUNTER, (), "Expired leases observed by the reclaimer"),
    (SCHEDULED_FIRE_METRIC, COUNTER, ("status",), "Schedule fires by outcome"),
    (SCHEDULED_MISFIRE_METRIC, COUNTER, (), "Schedules skipped for missing their fire time"),
    (DELIVERY_METRIC, COUNTER, ("status",), "Deliveries by resulting delivery status"),
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


def install_worker_metrics(app: FastAPI) -> None:
    """声明目录并注册真实 HTTP `GET /metrics`（复用 api-kit 进程内注册表）。"""
    for name, kind, _labels, help_text in CATALOG:
        declare_metric(name, kind, help=help_text)
    install_metrics(app)
