from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from muad_logging.redaction import redact_value
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskEvent, TaskExecution


class TaskEventType(StrEnum):
    CREATED = "CREATED"
    CLAIMED = "CLAIMED"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY = "RETRY"
    FAN_OUT = "FAN_OUT"
    FAN_IN = "FAN_IN"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    RECLAIMED = "RECLAIMED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    DELIVERY_SENT = "DELIVERY_SENT"
    DELIVERY_RETRY = "DELIVERY_RETRY"
    DELIVERY_FAILED = "DELIVERY_FAILED"


@dataclass(frozen=True)
class TaskEventSeed:
    tenant_id: str
    task_id: UUID
    event_type: TaskEventType
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None


async def _lock_tasks(session: AsyncSession, task_ids: Sequence[UUID]) -> None:
    """锁住父 Task 行，串行化同一 Task 的序号分配。

    `max(seq)` 必须在锁内读取：否则两个并发事务会读到同一个下界并写入相同
    seq，唯一约束 (task_id, seq) 会让其中之一在提交时失败。按 id 排序加锁，
    避免多 Task 批次之间互相死锁。
    """
    if not task_ids:
        return
    await session.execute(
        select(TaskExecution.id)
        .where(TaskExecution.id.in_(task_ids))
        .order_by(TaskExecution.id)
        .with_for_update()
    )


async def _seq_floor(session: AsyncSession, task_ids: Sequence[UUID]) -> dict[UUID, int]:
    if not task_ids:
        return {}
    rows = await session.execute(
        select(TaskEvent.task_id, func.max(TaskEvent.seq))
        .where(TaskEvent.task_id.in_(task_ids))
        .group_by(TaskEvent.task_id)
    )
    return {task_id: int(max_seq) for task_id, max_seq in rows.all()}


async def append_events(session: AsyncSession, seeds: Sequence[TaskEventSeed]) -> None:
    if not seeds:
        return
    unique_ids = tuple(sorted({seed.task_id for seed in seeds}, key=str))
    await _lock_tasks(session, unique_ids)
    floor = await _seq_floor(session, unique_ids)
    counters: dict[UUID, int] = {}
    for seed in seeds:
        seq = counters.get(seed.task_id, floor.get(seed.task_id, 0)) + 1
        counters[seed.task_id] = seq
        session.add(
            TaskEvent(
                tenant_id=seed.tenant_id,
                task_id=seed.task_id,
                seq=seq,
                event_type=str(seed.event_type),
                payload_json=redact_value(seed.payload),
                trace_id=seed.trace_id,
            )
        )


async def append_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    task_id: UUID,
    event_type: TaskEventType,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> None:
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=tenant_id,
                task_id=task_id,
                event_type=event_type,
                payload=payload or {},
                trace_id=trace_id,
            )
        ],
    )
