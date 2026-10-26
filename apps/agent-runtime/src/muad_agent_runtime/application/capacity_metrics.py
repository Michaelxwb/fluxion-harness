"""Runtime 容量/队列指标快照（TASK-005 S-13 / B-01）。

只读真实持久事实与进程内准入闸门，不做估算：

- `runtime_queue_depth{stage}`：本租户仍在初始排队（WAITING_RUNTIME/WAITING_MODEL）的 Run 数；
- `runtime_oldest_queue_age_seconds{stage}`：最老排队 Run 的等待秒数（PG `now()` 计算）；
- `runtime_execution_inflight`：已离开队列、正在执行的 Run 数；
- `runtime_open_runs`：非终态 Run 总数；
- `runtime_memory_job_queue{state}`：memory consolidation 的 PENDING/RUNNING/RETRY；
- `runtime_admission_inflight/waiting`：本进程准入闸门的在飞/排队数。

这些值让 HPA 与运维能按**队列**而不是只看 CPU 扩缩；没有流量时目录仍由 `metrics.CATALOG`
声明。采样失败只留警告并继续下一拍，不杀死维护循环。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import MemoryConsolidationJob, RunRecord
from ..metrics import (
    ADMISSION_INFLIGHT_METRIC,
    ADMISSION_WAITING_METRIC,
    EXECUTION_INFLIGHT_METRIC,
    MEMORY_JOB_QUEUE_METRIC,
    OLDEST_QUEUE_AGE_METRIC,
    OPEN_RUNS_METRIC,
    QUEUE_DEPTH_METRIC,
    record_gauge,
)
from .run_service import admission_gate_stats

logger = logging.getLogger(__name__)

QUEUE_STAGES: Final = ("WAITING_RUNTIME", "WAITING_MODEL")
ACTIVE_RUN_STATUSES: Final = ("CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT")
MEMORY_JOB_STATES: Final = ("PENDING", "RUNNING", "RETRY")


@dataclass(frozen=True, slots=True)
class CapacityGauges:
    queue_depth: dict[str, int]
    oldest_queue_age_sec: dict[str, float]
    execution_inflight: int
    open_runs: int
    memory_jobs: dict[str, int]
    admission_inflight: int
    admission_waiting: int


async def read_capacity_gauges(
    factory: SessionFactory, *, tenant_id: str
) -> CapacityGauges:
    age_expr = func.extract(
        "epoch", func.now() - func.coalesce(RunRecord.queued_at, RunRecord.create_time)
    )
    async with factory() as session:
        queue_rows = await session.execute(
            select(RunRecord.queue_stage, func.count(), func.max(age_expr))
            .where(
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status.in_(ACTIVE_RUN_STATUSES),
                RunRecord.queue_stage.in_(QUEUE_STAGES),
            )
            .group_by(RunRecord.queue_stage)
        )
        queue_depth = {stage: 0 for stage in QUEUE_STAGES}
        oldest = {stage: 0.0 for stage in QUEUE_STAGES}
        for stage, count, max_age in queue_rows:
            queue_depth[str(stage)] = int(count or 0)
            oldest[str(stage)] = float(max_age or 0.0)
        inflight = await session.scalar(
            select(func.count())
            .select_from(RunRecord)
            .where(
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status == "RUNNING",
                RunRecord.queue_stage.is_(None),
            )
        )
        open_runs = await session.scalar(
            select(func.count())
            .select_from(RunRecord)
            .where(
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        memory_rows = await session.execute(
            select(MemoryConsolidationJob.status, func.count())
            .where(
                MemoryConsolidationJob.tenant_id == tenant_id,
                MemoryConsolidationJob.is_deleted.is_(False),
            )
            .group_by(MemoryConsolidationJob.status)
        )
        memory = {state: 0 for state in MEMORY_JOB_STATES}
        for state, count in memory_rows:
            if str(state) in memory:
                memory[str(state)] = int(count or 0)
    stats = admission_gate_stats()
    return CapacityGauges(
        queue_depth=queue_depth,
        oldest_queue_age_sec=oldest,
        execution_inflight=int(inflight or 0),
        open_runs=int(open_runs or 0),
        memory_jobs=memory,
        admission_inflight=stats.inflight,
        admission_waiting=stats.waiting,
    )


def publish_capacity_gauges(gauges: CapacityGauges) -> None:
    for stage in QUEUE_STAGES:
        record_gauge(
            QUEUE_DEPTH_METRIC,
            gauges.queue_depth.get(stage, 0),
            {"stage": stage},
        )
        record_gauge(
            OLDEST_QUEUE_AGE_METRIC,
            gauges.oldest_queue_age_sec.get(stage, 0.0),
            {"stage": stage},
        )
    record_gauge(EXECUTION_INFLIGHT_METRIC, gauges.execution_inflight)
    record_gauge(OPEN_RUNS_METRIC, gauges.open_runs)
    for state in MEMORY_JOB_STATES:
        record_gauge(
            MEMORY_JOB_QUEUE_METRIC,
            gauges.memory_jobs.get(state, 0),
            {"state": state},
        )
    record_gauge(ADMISSION_INFLIGHT_METRIC, gauges.admission_inflight)
    record_gauge(ADMISSION_WAITING_METRIC, gauges.admission_waiting)


async def capacity_metrics_loop(
    factory: SessionFactory, *, tenant_id: str, interval_sec: float
) -> None:
    if interval_sec <= 0:
        raise ValueError("capacity metrics interval must be positive")
    while True:
        try:
            gauges = await read_capacity_gauges(factory, tenant_id=tenant_id)
            publish_capacity_gauges(gauges)
        except Exception as exc:  # noqa: BLE001 - 采样失败不杀死维护循环
            logger.warning(
                "capacity_metrics_sample_failed", extra={"error_type": type(exc).__name__}
            )
        await asyncio.sleep(interval_sec)
