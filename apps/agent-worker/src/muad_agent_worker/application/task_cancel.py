"""BATCH Parent 取消时向下级联（设计 §3.2.2 取消语义 + §3.2.4 Parent/Child）。

用户取消 Parent 即取消整个批量意图：同事务内把所有非终态后代
- `QUEUED/WAITING` → CAS `CANCELLED`（写 `CANCELLED` 事件）；
- `RUNNING` → `cancel_requested=true`，由持有 lease 的 Worker 在检查点协作停止。

按 `parent_id` 逐层下探，嵌套批量同样覆盖；已终态的后代不改。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from muad_contracts import TaskStatus
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskExecution
from .task_events import TaskEventSeed, TaskEventType, append_events

IDLE_STATUSES = (str(TaskStatus.QUEUED), str(TaskStatus.WAITING))
MAX_CASCADE_DEPTH = 8


async def _cancel_idle_children(
    session: AsyncSession, parent_ids: list[uuid.UUID], moment: datetime
) -> list[tuple[uuid.UUID, str]]:
    result = await session.execute(
        update(TaskExecution)
        .where(
            TaskExecution.parent_id.in_(parent_ids),
            TaskExecution.status.in_(IDLE_STATUSES),
            TaskExecution.is_deleted.is_(False),
        )
        .values(
            status=str(TaskStatus.CANCELLED),
            cancel_requested=True,
            finished_at=moment,
            lease_owner=None,
            lease_until=None,
            update_time=moment,
        )
        .returning(TaskExecution.id, TaskExecution.tenant_id)
        .execution_options(synchronize_session=False)
    )
    return [(row[0], row[1]) for row in result.all()]


async def _flag_running_children(
    session: AsyncSession, parent_ids: list[uuid.UUID], moment: datetime
) -> list[tuple[uuid.UUID, str]]:
    result = await session.execute(
        update(TaskExecution)
        .where(
            TaskExecution.parent_id.in_(parent_ids),
            TaskExecution.status == str(TaskStatus.RUNNING),
            TaskExecution.cancel_requested.is_(False),
            TaskExecution.is_deleted.is_(False),
        )
        .values(cancel_requested=True, update_time=moment)
        .returning(TaskExecution.id, TaskExecution.tenant_id)
        .execution_options(synchronize_session=False)
    )
    return [(row[0], row[1]) for row in result.all()]


async def cancel_children(session: AsyncSession, parent: TaskExecution, moment: datetime) -> int:
    """级联取消 `parent` 的全部非终态后代，返回受影响的 Child 数。"""
    frontier = [parent.id]
    affected = 0
    for _ in range(MAX_CASCADE_DEPTH):
        cancelled = await _cancel_idle_children(session, frontier, moment)
        flagged = await _flag_running_children(session, frontier, moment)
        seeds = [
            TaskEventSeed(
                tenant_id=tenant_id,
                task_id=task_id,
                event_type=event_type,
                payload={"cascade_from": str(parent.id)},
            )
            for rows, event_type in (
                (cancelled, TaskEventType.CANCELLED),
                (flagged, TaskEventType.CANCEL_REQUESTED),
            )
            for task_id, tenant_id in rows
        ]
        await append_events(session, seeds)
        affected += len(seeds)
        frontier = [task_id for task_id, _ in (*cancelled, *flagged)]
        if not frontier:
            break
    return affected


__all__ = ["cancel_children"]
