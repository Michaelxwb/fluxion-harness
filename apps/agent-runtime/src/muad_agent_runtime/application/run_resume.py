"""Conversation → Run → Interrupt locks and post-lock absolute deadline fences."""

from datetime import datetime

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import RunStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import Conversation, RunInterrupt, RunRecord


def ensure_human_resume(run: RunRecord, now: datetime) -> None:
    if run.status != RunStatus.WAITING_INPUT or run.cancel_requested:
        raise AppError(ErrorCode.RUN_BUSY)
    if run.deadline_at is None or run.deadline_at <= now:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_EXPIRED)


async def lock_human_resume(session: AsyncSession, run: RunRecord) -> list[RunInterrupt]:
    """锁定该 Run 的**全部**中断：终态的记忆提议也要进入 `prepare_confirmation` 的裁决。

    只锁 WAITING 会让「被撤销/已过期提议」的确认静默降级成普通消息；把终态行一并锁住，
    确认路径才能对它显式拒绝（`MEMORY_CONFIRMATION_EXPIRED`）且与撤销方互斥。
    """
    owned = await session.scalar(
        select(Conversation.id)
        .where(
            Conversation.id == run.conversation_id,
            Conversation.tenant_id == run.tenant_id,
            Conversation.user_id == run.user_id,
            Conversation.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if owned is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    locked = await session.scalar(
        select(RunRecord)
        .where(
            RunRecord.id == run.id,
            RunRecord.tenant_id == run.tenant_id,
            RunRecord.conversation_id == run.conversation_id,
            RunRecord.user_id == run.user_id,
            RunRecord.is_deleted.is_(False),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    ensure_human_resume(locked, await database_now(session))
    return list(
        await session.scalars(
            select(RunInterrupt)
            .where(
                RunInterrupt.tenant_id == run.tenant_id,
                RunInterrupt.run_id == run.id,
                RunInterrupt.conversation_id == run.conversation_id,
                RunInterrupt.is_deleted.is_(False),
            )
            .order_by(RunInterrupt.id)
            .with_for_update()
        )
    )


async def transition_human_resume(session: AsyncSession, run: RunRecord) -> None:
    now = await database_now(session)
    ensure_human_resume(run, now)
    changed = await session.scalar(
        update(RunRecord)
        .where(
            RunRecord.id == run.id,
            RunRecord.tenant_id == run.tenant_id,
            RunRecord.is_deleted.is_(False),
            RunRecord.status == RunStatus.WAITING_INPUT,
            RunRecord.cancel_requested.is_(False),
            RunRecord.deadline_at > now,
        )
        .values(status=RunStatus.CREATED, lease_owner=None, lease_until=None, update_time=now)
        .returning(RunRecord.id)
    )
    if changed is None:
        raise AppError(ErrorCode.RUN_BUSY)


async def resolve_generic_interrupts(session: AsyncSession, rows: list[RunInterrupt], text: str) -> None:
    now = await database_now(session)
    for row in rows:
        if row.status != "WAITING":
            continue
        if row.interrupt_type == "MEMORY_CONFIRMATION":
            raise RuntimeError("memory confirmation must use the dedicated dispatcher")
        row.status, row.resolved_at, row.update_time = "RESOLVED", now, now
        row.resolution_json = {"input": text}
