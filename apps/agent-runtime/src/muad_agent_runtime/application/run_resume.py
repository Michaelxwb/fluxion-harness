"""Conversation → Run → Interrupt locks and post-lock absolute deadline fences."""

from datetime import datetime

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import RunStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import Conversation, RunInterrupt, RunRecord
from .run_admission import QUEUE_STAGE_WAITING_RUNTIME


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
    deadline_at = run.deadline_at
    if deadline_at is None:  # ensure_human_resume 已保证；这里只做静态类型收窄
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_EXPIRED)
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
        .values(
            status=RunStatus.CREATED,
            lease_owner=None,
            lease_until=None,
            update_time=now,
            # human resume 回到"等待运行时领取"的排队态：排队投影必须重新有效，否则受理回执
            # 的 `execution_status` 投影会因缺少排队窗口而抛错（Run 已通过绝对期限复核，
            # 这里直接用该绝对期限作为排队上界，不另造新的等待预算）。
            queue_stage=QUEUE_STAGE_WAITING_RUNTIME,
            queued_at=now,
            queue_deadline_at=deadline_at,
        )
        .returning(RunRecord.id)
    )
    if changed is None:
        raise AppError(ErrorCode.RUN_BUSY)
    # 同一 Session 内的对象仍带旧投影：续跑受理回执直接用它投影，必须同步为落库事实。
    run.status = RunStatus.CREATED
    run.lease_owner = None
    run.lease_until = None
    run.update_time = now
    run.queue_stage = QUEUE_STAGE_WAITING_RUNTIME
    run.queued_at = now
    run.queue_deadline_at = deadline_at


async def resolve_generic_interrupts(session: AsyncSession, rows: list[RunInterrupt], text: str) -> None:
    now = await database_now(session)
    for row in rows:
        if row.status != "WAITING":
            continue
        if row.interrupt_type == "MEMORY_CONFIRMATION":
            raise RuntimeError("memory confirmation must use the dedicated dispatcher")
        row.status, row.resolved_at, row.update_time = "RESOLVED", now, now
        row.resolution_json = {"input": text}
