"""多态审计事件：`(entity_type, entity_id)` 定位主体，`seq` 每实体递增（设计 §3.3.9/§3.5.10）。

事件是审计，实体行才是状态权威：写入方负责在与业务变更同一事务内追加，失败一起回滚。
`occurred_at` 是业务发生时间，与入库时间 `create_time` 分开。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID

from muad_logging.redaction import redact_value
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import (
    ScheduleFire,
    TaskDelivery,
    TaskEvent,
    TaskExecution,
    TaskHumanInteraction,
    TaskSchedule,
)


class EntityType(StrEnum):
    SCHEDULE = "SCHEDULE"
    FIRE = "FIRE"
    EXECUTION = "EXECUTION"
    HUMAN = "HUMAN"
    DELIVERY = "DELIVERY"
    ARTIFACT = "ARTIFACT"
    ASYNC = "ASYNC"


class TaskEventType(StrEnum):
    """§3.5.10 的事件名全集；同一名字可挂在多个实体上（如 CREATED/CANCELLED）。"""

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    PAUSED = "PAUSED"
    RESUMED = "RESUMED"
    CANCELLED = "CANCELLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    ENDED = "ENDED"
    DST_GAP_SKIPPED = "DST_GAP_SKIPPED"
    DST_FOLD_SECOND_SKIPPED = "DST_FOLD_SECOND_SKIPPED"
    EXECUTION_CREATED = "EXECUTION_CREATED"
    SKIPPED = "SKIPPED"
    FAILED_ADMISSION = "FAILED_ADMISSION"
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RECLAIMED = "RECLAIMED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    REQUESTED = "REQUESTED"
    RESOLVED = "RESOLVED"
    TIMED_OUT = "TIMED_OUT"
    ENQUEUED = "ENQUEUED"
    SENT = "SENT"
    REGISTERED = "REGISTERED"
    DOWNLOADED = "DOWNLOADED"
    SUBMITTED = "SUBMITTED"
    RESULT_RECEIVED = "RESULT_RECEIVED"


ENTITY_EVENT_TYPES: dict[EntityType, frozenset[TaskEventType]] = {
    EntityType.SCHEDULE: frozenset(
        {
            TaskEventType.CREATED,
            TaskEventType.UPDATED,
            TaskEventType.PAUSED,
            TaskEventType.RESUMED,
            TaskEventType.CANCELLED,
            TaskEventType.NEEDS_ATTENTION,
            TaskEventType.ENDED,
            TaskEventType.DST_GAP_SKIPPED,
            TaskEventType.DST_FOLD_SECOND_SKIPPED,
        }
    ),
    EntityType.FIRE: frozenset(
        {
            TaskEventType.CREATED,
            TaskEventType.EXECUTION_CREATED,
            TaskEventType.SKIPPED,
            TaskEventType.FAILED_ADMISSION,
            TaskEventType.CANCELLED,
        }
    ),
    EntityType.EXECUTION: frozenset(
        {
            TaskEventType.QUEUED,
            TaskEventType.CLAIMED,
            TaskEventType.WAITING_EXTERNAL,
            TaskEventType.WAITING_HUMAN,
            TaskEventType.RESUMED,
            TaskEventType.COMPLETED,
            TaskEventType.FAILED,
            TaskEventType.CANCELLED,
            TaskEventType.RECLAIMED,
            TaskEventType.DEADLINE_EXCEEDED,
        }
    ),
    EntityType.HUMAN: frozenset(
        {
            TaskEventType.REQUESTED,
            TaskEventType.RESOLVED,
            TaskEventType.TIMED_OUT,
            TaskEventType.CANCELLED,
        }
    ),
    EntityType.DELIVERY: frozenset(
        {
            TaskEventType.ENQUEUED,
            TaskEventType.SENT,
            TaskEventType.FAILED,
            TaskEventType.CANCELLED,
        }
    ),
    EntityType.ARTIFACT: frozenset({TaskEventType.REGISTERED, TaskEventType.DOWNLOADED}),
    EntityType.ASYNC: frozenset(
        {
            TaskEventType.SUBMITTED,
            TaskEventType.RESULT_RECEIVED,
            TaskEventType.CANCELLED,
            TaskEventType.FAILED,
        }
    ),
}

#: 只有 task schema 内的实体有物理行可 `FOR UPDATE`；ARTIFACT/ASYNC 在别的 schema，
#: 退化为实体级 advisory lock（§3.5.10 的两种 seq 分配方式）。
_ENTITY_ROWS: dict[EntityType, Any] = {
    EntityType.SCHEDULE: TaskSchedule,
    EntityType.FIRE: ScheduleFire,
    EntityType.EXECUTION: TaskExecution,
    EntityType.HUMAN: TaskHumanInteraction,
    EntityType.DELIVERY: TaskDelivery,
}

#: (tenant_id, entity_type, entity_id)：唯一键与序号下界都以租户为界。
EntityKey = tuple[str, EntityType, UUID]


@dataclass(frozen=True)
class TaskEventSeed:
    tenant_id: str
    entity_type: EntityType
    entity_id: UUID
    event_type: TaskEventType
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    occurred_at: datetime | None = None


async def _lock_entities(session: AsyncSession, keys: Sequence[EntityKey]) -> None:
    """串行化同一实体的序号分配。

    `max(seq)` 必须在锁内读取：否则两个并发事务读到同一个下界并写入相同 seq，
    唯一约束 `(tenant_id, entity_type, entity_id, seq)` 会让其中之一在提交时失败。
    行锁按 (entity_type, entity_id) 排序获取，避免多实体批次互相死锁；没有物理行的
    实体退化为 `pg_advisory_xact_lock`（事务级，与行锁一起在提交时释放）。锁一律带
    租户谓词：别的租户的同 id 行不参与本租户的序号分配。
    """
    for entity_type, model in _ENTITY_ROWS.items():
        owned = sorted(
            {(tenant_id, entity_id) for tenant_id, kind, entity_id in keys if kind is entity_type},
            key=lambda item: (item[0], str(item[1])),
        )
        for tenant_id, entity_id in owned:
            await session.execute(
                select(model.id)
                .where(
                    model.id == entity_id,
                    model.tenant_id == tenant_id,
                    model.is_deleted.is_(False),
                )
                .with_for_update()
            )
    for tenant_id, entity_type, entity_id in sorted(
        set(keys), key=lambda key: (key[1].value, key[0], str(key[2]))
    ):
        if entity_type in _ENTITY_ROWS:
            continue
        await session.execute(
            select(func.pg_advisory_xact_lock(_advisory_key(tenant_id, entity_type, entity_id)))
        )


def _advisory_key(tenant_id: str, entity_type: EntityType, entity_id: UUID) -> int:
    identity = f"task_event:{tenant_id}:{entity_type.value}:{entity_id}"
    return int.from_bytes(sha256(identity.encode()).digest()[:8], "big", signed=True)


async def _seq_floor(session: AsyncSession, keys: Sequence[EntityKey]) -> dict[EntityKey, int]:
    if not keys:
        return {}
    rows = await session.execute(
        select(
            TaskEvent.tenant_id,
            TaskEvent.entity_type,
            TaskEvent.entity_id,
            func.max(TaskEvent.seq),
        )
        .where(
            TaskEvent.tenant_id.in_([tenant_id for tenant_id, _, _ in keys]),
            TaskEvent.entity_type.in_([kind.value for _, kind, _ in keys]),
            TaskEvent.entity_id.in_([entity_id for _, _, entity_id in keys]),
        )
        .group_by(TaskEvent.tenant_id, TaskEvent.entity_type, TaskEvent.entity_id)
    )
    wanted = set(keys)
    return {
        (tenant_id, EntityType(entity_type), entity_id): int(max_seq)
        for tenant_id, entity_type, entity_id, max_seq in rows.all()
        if (tenant_id, EntityType(entity_type), entity_id) in wanted
    }


def _check_event_type(seed: TaskEventSeed) -> None:
    allowed = ENTITY_EVENT_TYPES[seed.entity_type]
    if seed.event_type not in allowed:
        raise ValueError(
            f"event {seed.event_type} is not a {seed.entity_type} event"
        )


async def append_events(session: AsyncSession, seeds: Sequence[TaskEventSeed]) -> list[TaskEvent]:
    if not seeds:
        return []
    for seed in seeds:
        _check_event_type(seed)
    keys = tuple((seed.tenant_id, seed.entity_type, seed.entity_id) for seed in seeds)
    await _lock_entities(session, keys)
    floor = await _seq_floor(session, tuple(set(keys)))
    counters: dict[EntityKey, int] = {}
    events: list[TaskEvent] = []
    for seed in seeds:
        key = (seed.tenant_id, seed.entity_type, seed.entity_id)
        seq = counters.get(key, floor.get(key, 0)) + 1
        counters[key] = seq
        event = TaskEvent(
            tenant_id=seed.tenant_id,
            entity_type=seed.entity_type.value,
            entity_id=seed.entity_id,
            seq=seq,
            event_type=str(seed.event_type),
            payload_json=redact_value(seed.payload),
            occurred_at=seed.occurred_at or datetime.now(UTC),
            trace_id=seed.trace_id,
        )
        session.add(event)
        events.append(event)
    return events


async def append_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    entity_type: EntityType,
    entity_id: UUID,
    event_type: TaskEventType,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    occurred_at: datetime | None = None,
) -> TaskEvent:
    events = await append_events(
        session,
        [
            TaskEventSeed(
                tenant_id=tenant_id,
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                payload=payload or {},
                trace_id=trace_id,
                occurred_at=occurred_at,
            )
        ],
    )
    return events[0]
