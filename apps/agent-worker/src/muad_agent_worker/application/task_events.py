from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskEvent


class TaskEventType(StrEnum):
    CREATED = "CREATED"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY = "RETRY"
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
    unique_ids = tuple({seed.task_id for seed in seeds})
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
                payload_json=seed.payload,
            )
        )


async def append_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    task_id: UUID,
    event_type: TaskEventType,
    payload: dict[str, Any] | None = None,
) -> None:
    await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=tenant_id,
                task_id=task_id,
                event_type=event_type,
                payload=payload or {},
            )
        ],
    )
