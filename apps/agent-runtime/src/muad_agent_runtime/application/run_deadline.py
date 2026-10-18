"""Absolute deadlines apply while running, waiting for input or waiting for tools.

**时钟口径（design §3.5 时间，B-235）**：绝对期限的候选预筛与逐行收尾都按 PG 服务端时钟
（`database_now`，行锁之后取值）；进程墙钟只用于计时与展示。`now` 是显式的测试注入点。
"""

from datetime import datetime

from muad_contracts import RunStatus
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import RunInterrupt, RunRecord, RunSubmission
from .async_tools.operations import cancel_operations
from .run_admission import release_admission_in
from .run_events import EventWriter
from .tool_result_receipts import lock_run_source

ACTIVE_STATUSES = (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.WAITING_INPUT, RunStatus.WAITING_TOOL)


async def expire_runs(factory: SessionFactory, *, now: datetime | None = None, limit: int = 128) -> int:
    """绝对期限到期的活跃 Run 收尾；候选与逐行判定都按 PG 时钟（`now` 仅测试注入）。"""
    async with factory() as session:
        candidates = tuple(
            await session.scalars(
                select(RunRecord)
                .where(
                    RunRecord.status.in_(ACTIVE_STATUSES),
                    RunRecord.deadline_at <= func.now(),
                    RunRecord.is_deleted.is_(False),
                )
                .order_by(RunRecord.deadline_at, RunRecord.id)
                .limit(limit)
            )
        )
    expired = 0
    for candidate in candidates:
        expired += await _expire_one(factory, candidate, now)
    return expired


async def _expire_one(
    factory: SessionFactory, candidate: RunRecord, now: datetime | None
) -> int:
    async with factory() as session, session.begin():
        run, checkpoint = await lock_run_source(session, candidate.tenant_id, candidate)
        moment = now if now is not None else await database_now(session)
        if run.status not in ACTIVE_STATUSES or run.deadline_at is None or run.deadline_at > moment:
            return 0
        await cancel_operations(session, run)
        run.status, run.error_code, run.error_message = (
            RunStatus.FAILED,
            "RUN_DEADLINE_EXCEEDED",
            "RUN_DEADLINE_EXCEEDED",
        )
        run.lease_owner, run.lease_until, run.end_time, run.update_time = None, None, moment, moment
        run.queue_stage = None
        # 绝对期限收尾同样释放准入份额：与终态同事务，重复扫描不再递减（CAS 幂等）。
        await release_admission_in(session, run=run)
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
        await _close_waits(session, run, seq, moment)
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
