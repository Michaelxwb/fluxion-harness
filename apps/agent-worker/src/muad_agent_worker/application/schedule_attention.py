"""把来源 Schedule 置为 `NEEDS_ATTENTION` 的唯一写入点（design §4.6/§4.7）。

投递路由永久失效时，用户必须看到“需要修复”，而不是一条永远重试的投递记录。只改
**Schedule** 的调度状态并写审计事件；Execution 的业务终态由执行侧持有，这里不碰。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskExecution, TaskSchedule
from .task_events import EntityType, TaskEventType, append_event

logger = logging.getLogger(__name__)

ACTIVE_SCHEDULE = "ACTIVE"
NEEDS_ATTENTION = "NEEDS_ATTENTION"


async def flag_schedule_attention(
    session: AsyncSession,
    *,
    tenant_id: str,
    execution_id: uuid.UUID,
    code: str,
    now: datetime,
) -> bool:
    """路由失效：来源 Schedule 置 `NEEDS_ATTENTION` 并写明原因；返回是否发生变更。"""
    schedule = await session.scalar(
        select(TaskSchedule)
        .join(
            TaskExecution,
            (TaskExecution.schedule_id == TaskSchedule.id)
            & (TaskExecution.id == execution_id)
            & (TaskExecution.tenant_id == tenant_id)
            & (TaskExecution.is_deleted.is_(False)),
        )
        .where(
            TaskSchedule.tenant_id == tenant_id,
            TaskSchedule.is_deleted.is_(False),
        )
        .with_for_update(of=TaskSchedule)
    )
    if schedule is None or schedule.status != ACTIVE_SCHEDULE:
        return False
    schedule.status = NEEDS_ATTENTION
    schedule.attention_reason = code[:64]
    schedule.attention_since = now
    schedule.next_fire_at = None
    schedule.update_time = now
    await append_event(
        session,
        tenant_id=tenant_id,
        entity_type=EntityType.SCHEDULE,
        entity_id=schedule.id,
        event_type=TaskEventType.NEEDS_ATTENTION,
        occurred_at=now,
        payload={"reason": code, "execution_id": str(execution_id)},
    )
    logger.warning("schedule_needs_attention schedule_id=%s reason=%s", schedule.id, code)
    return True


__all__ = ["ACTIVE_SCHEDULE", "NEEDS_ATTENTION", "flag_schedule_attention"]
