from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from muad_common import SharedSettings
from muad_contracts import TaskStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.task_events import TaskEventType, append_event
from ..infrastructure.models.task import TaskExecution
from ..metrics import TASK_QUEUE_DEPTH_METRIC, increment, record_gauge

TASK_CLAIM_TOTAL = "task_claim_total"

CLAIMABLE_STATUSES = (str(TaskStatus.QUEUED), str(TaskStatus.WAITING))


def _claimable_conditions(moment: datetime) -> tuple[Any, ...]:
    """可 claim 条件：`task_queue_depth` 与 claim 本身共用同一口径。"""
    return (
        TaskExecution.status.in_(CLAIMABLE_STATUSES),
        TaskExecution.not_before <= moment,
        TaskExecution.cancel_requested.is_(False),
        TaskExecution.is_deleted.is_(False),
    )


async def _claimable_depth(session: AsyncSession, moment: datetime) -> int:
    depth = await session.scalar(
        select(func.count()).select_from(TaskExecution).where(*_claimable_conditions(moment))
    )
    return int(depth or 0)


class TaskClaimer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SharedSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or SharedSettings()

    async def claim_one(self, instance_id: str, *, now: datetime | None = None) -> TaskExecution | None:
        claimed_at = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                record_gauge(TASK_QUEUE_DEPTH_METRIC, float(await _claimable_depth(session, claimed_at)))
                task = (
                    await session.execute(
                        select(TaskExecution)
                        .where(*_claimable_conditions(claimed_at))
                        .order_by(TaskExecution.priority.asc(), TaskExecution.create_time.asc())
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if task is None:
                    return None
                # 重试预算只记 QUEUED→RUNNING（首次/重试/reclaim）；WAITING 到期再 claim
                # 是外部轮询续接，不消耗 attempt，否则等待几轮后任何失败都会直接 FAILED。
                resumed_from_wait = task.status == str(TaskStatus.WAITING)
                task.status = str(TaskStatus.RUNNING)
                task.lease_owner = instance_id
                task.lease_until = claimed_at + timedelta(seconds=self._settings.task_lease_sec)
                task.heartbeat_at = claimed_at
                if not resumed_from_wait:
                    task.attempt += 1
                if task.started_at is None:
                    task.started_at = claimed_at
                task.update_time = claimed_at
                await append_event(
                    session,
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    event_type=TaskEventType.CLAIMED,
                    payload={
                        "lease_owner": instance_id,
                        "attempt": task.attempt,
                        "lease_until": task.lease_until.isoformat(),
                        "resumed_from_wait": resumed_from_wait,
                    },
                )
                await session.flush()
        increment(TASK_CLAIM_TOTAL)
        return task
