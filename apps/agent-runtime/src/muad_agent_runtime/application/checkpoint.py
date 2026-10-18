"""Interrupt Checkpoint：run_interrupt WAITING 持久化 + 租约释放 + 定位/resolve。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.suspension import MemorySuspendRequest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import RunStatus
from muad_contracts.memory import MemoryConfirmationProposal, memory_hash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.db import SessionFactory, SessionFactoryProvider, get_session_factory
from ..infrastructure.models.runtime import RunInterrupt, RunRecord
from .async_tools.continuation_service import ExecutionIdentity, _write_checkpoint
from .channel_notifications import (
    KIND_RUN_WAITING_INPUT,
    run_waiting_delivery_key,
    write_run_notification,
)
from .memory_proposals import (
    confirmation_source,
    ensure_no_memory_confirmation,
    locked_confirmation_execution,
)
from .run_events import EventWriter

STATUS_WAITING_INPUT = RunStatus.WAITING_INPUT
STATUS_RUNNING = RunStatus.RUNNING
INTERRUPT_WAITING = "WAITING"
INTERRUPT_RESOLVED = "RESOLVED"


async def suspend_memory_confirmation(
    factory: SessionFactory,
    identity: ExecutionIdentity,
    request: MemorySuspendRequest,
    progress: RunnerCheckpoint,
) -> None:
    """Conversation/Run locks fence a single atomic complete-round waiting transition."""
    async with factory.begin() as session:
        run, saved, now = await locked_confirmation_execution(session, identity)
        await ensure_no_memory_confirmation(session, identity)
        proposal = await _validate_memory_proposal(session, run, request, now)
        saved = await _write_checkpoint(session, run, saved, progress, now)
        session.add(
            RunInterrupt(
                id=request.interrupt_id,
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                interrupt_type="MEMORY_CONFIRMATION",
                prompt_text=request.prompt,
                options_json=[],
                status=INTERRUPT_WAITING,
                proposal_json=proposal.model_dump(mode="json"),
                expires_at=proposal.expires_at,
            )
        )
        confirmation_event_id = uuid.uuid4()
        saved.context_upto_seq = await EventWriter(session).append(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            submission_id=proposal.submission_id,
            event_type="MEMORY_CONFIRMATION_REQUIRED",
            stream_type="memory.confirmation_required",
            payload={
                "proposal_id": str(request.interrupt_id),
                "prompt": request.prompt,
                "expires_at": proposal.expires_at.isoformat(),
                "proposal_hash": memory_hash(proposal.model_dump(mode="json")),
            },
            event_id=confirmation_event_id,
        )
        run.status, run.lease_owner, run.lease_until, run.update_time = STATUS_WAITING_INPUT, None, None, now
        await _notify_waiting_confirmation(
            session,
            run=run,
            interrupt_id=request.interrupt_id,
            event_id=confirmation_event_id,
        )


async def _notify_waiting_confirmation(
    session: AsyncSession,
    *,
    run: RunRecord,
    interrupt_id: uuid.UUID,
    event_id: uuid.UUID,
) -> None:
    """待确认与用户通知**同一事务**（design §3.6）：feed 锁在 Conversation/Run 锁之后；
    通知写不进去就整体回滚，绝不留下"等确认但没有可发现通知"的会话。
    """
    await write_run_notification(
        session,
        run=run,
        kind=KIND_RUN_WAITING_INPUT,
        delivery_key=run_waiting_delivery_key(run.id, interrupt_id),
        event_id=event_id,
    )


async def _validate_memory_proposal(
    session: AsyncSession,
    run: RunRecord,
    request: MemorySuspendRequest,
    now: datetime,
) -> MemoryConfirmationProposal:
    proposal = MemoryConfirmationProposal.model_validate(request.proposal.model_dump(mode="json"))
    event = await confirmation_source(session, run, proposal.submission_id)
    if (
        (proposal.tenant_id, proposal.user_id, proposal.run_id) != (run.tenant_id, run.user_id, run.id)
        or proposal.evidence.source_event_id != event.id
        or proposal.evidence.source_seq != event.seq
        or proposal.expires_at <= now
        or (run.deadline_at is not None and proposal.expires_at > run.deadline_at)
    ):
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    return proposal


async def checkpoint_interrupt(
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    lease_owner: str,
    interrupt_type: str,
    prompt_text: str,
    options_json: list[Any],
    checkpoint_state: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    factory = session_factory or get_session_factory()
    async with factory() as session:
        interrupt = RunInterrupt(
            tenant_id=tenant_id,
            run_id=run_id,
            conversation_id=conversation_id,
            interrupt_type=interrupt_type,
            prompt_text=prompt_text,
            options_json=options_json,
            status=INTERRUPT_WAITING,
        )
        session.add(interrupt)
        run = await session.get(RunRecord, run_id)
        if run is not None:
            run.status = STATUS_WAITING_INPUT
            run.lease_until = None  # 释放租约（时间维度）；lease_owner 保留为最后执行者记录
        # Conversation.status 仅允许 ACTIVE/ARCHIVED，暂停状态在 run/interrupt 上表达
        await session.commit()
        interrupt_id = interrupt.id
    return {
        "interrupt_id": str(interrupt_id),
        "kind": interrupt_type,
        "prompt": prompt_text,
        "checkpoint_state": checkpoint_state,
    }


async def load_waiting_interrupt(
    run_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any] | None:
    factory = session_factory or get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(RunInterrupt).where(
                    RunInterrupt.run_id == run_id,
                    RunInterrupt.status == INTERRUPT_WAITING,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "interrupt_id": str(row.id),
            "run_id": str(row.run_id),
            "conversation_id": str(row.conversation_id),
            "interrupt_type": row.interrupt_type,
            "prompt_text": row.prompt_text,
            "options_json": row.options_json,
        }


class InterruptCheckpoint:
    """进程重建后仍可经 DB 定位等待点（业务事实不依赖进程内存）。"""

    def __init__(self, *, session_factory: SessionFactoryProvider) -> None:
        self._session_factory = session_factory

    async def locate_waiting(self, *, run_id: uuid.UUID) -> dict[str, Any] | None:
        return await load_waiting_interrupt(run_id, session_factory=self._session_factory())

    async def resolve(
        self,
        *,
        run_id: uuid.UUID,
        lease_owner: str | None,
        resolution_json: dict[str, Any],
    ) -> None:
        """推进 WAITING interrupt：仅持有租约/合法接管者（lease_owner=None resume 接管）可操作。"""
        async with self._session_factory()() as session:
            row = (
                await session.execute(
                    select(RunInterrupt).where(
                        RunInterrupt.run_id == run_id,
                        RunInterrupt.status == INTERRUPT_WAITING,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            run = await session.get(RunRecord, run_id)
            current_owner = run.lease_owner if run is not None else None
            if current_owner is not None and lease_owner is not None and current_owner != lease_owner:
                raise PermissionError("only the lease owner can resolve the interrupt")
            row.status = INTERRUPT_RESOLVED
            row.resolution_json = resolution_json
            row.resolved_at = datetime.now(UTC)
            if run is not None:
                run.status = STATUS_RUNNING
                run.lease_owner = None  # resume 由新 submission 接管
            await session.commit()


# 兼容直接调用（事务内调用方传入 session）
async def release_lease_and_wait(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    run = await session.get(RunRecord, run_id)
    if run is not None:
        run.status = STATUS_WAITING_INPUT
        run.lease_owner = None
        run.lease_until = None
    await session.flush()
