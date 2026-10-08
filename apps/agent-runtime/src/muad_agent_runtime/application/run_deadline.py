"""Absolute deadlines apply while running, waiting for input or waiting for tools."""

from datetime import UTC, datetime

from muad_contracts import RunStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import RunInterrupt, RunRecord, RunSubmission
from .async_tools.operations import cancel_operations
from .run_events import EventWriter
from .tool_result_receipts import lock_run_source

ACTIVE_STATUSES = (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.WAITING_INPUT, RunStatus.WAITING_TOOL)


async def expire_runs(factory: SessionFactory, *, now: datetime | None = None, limit: int = 128) -> int:
    instant = now or datetime.now(UTC)
    async with factory() as session:
        candidates = tuple(
            await session.scalars(
                select(RunRecord)
                .where(
                    RunRecord.status.in_(ACTIVE_STATUSES),
                    RunRecord.deadline_at <= instant,
                    RunRecord.is_deleted.is_(False),
                )
                .order_by(RunRecord.deadline_at, RunRecord.id)
                .limit(limit)
            )
        )
    expired = 0
    for candidate in candidates:
        expired += await _expire_one(factory, candidate, instant)
    return expired


async def _expire_one(factory: SessionFactory, candidate: RunRecord, now: datetime) -> int:
    async with factory() as session, session.begin():
        run, checkpoint = await lock_run_source(session, candidate.tenant_id, candidate)
        if run.status not in ACTIVE_STATUSES or run.deadline_at is None or run.deadline_at > now:
            return 0
        await cancel_operations(session, run)
        run.status, run.error_code, run.error_message = (
            RunStatus.FAILED,
            "RUN_DEADLINE_EXCEEDED",
            "RUN_DEADLINE_EXCEEDED",
        )
        run.lease_owner, run.lease_until, run.end_time, run.update_time = None, None, now, now
        seq = await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            event_type="RUN_FAILED",
            stream_type="run.failed",
            payload={"status": "FAILED", "error_code": run.error_code},
        )
        if checkpoint is not None:
            checkpoint.ready = False
        await _close_waits(session, run, seq, now)
    return 1


async def _close_waits(session: AsyncSession, run: RunRecord, seq: int, now: datetime) -> None:
    await session.execute(update(RunInterrupt).where(
        RunInterrupt.tenant_id == run.tenant_id, RunInterrupt.run_id == run.id,
        RunInterrupt.is_deleted.is_(False), RunInterrupt.status == "WAITING",
    ).values(status="CANCELLED", resolution_json={"reason": "RUN_DEADLINE_EXCEEDED"},
        resolved_at=now, update_time=now))
    await session.execute(update(RunSubmission).where(
        RunSubmission.tenant_id == run.tenant_id, RunSubmission.run_id == run.id,
        RunSubmission.is_deleted.is_(False), RunSubmission.status == "OPEN",
    ).values(status="CLOSED", last_seq=seq, update_time=now,
        terminal_result_json={"status": "FAILED", "error_code": run.error_code}))
