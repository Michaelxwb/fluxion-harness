"""Parent-first expiration of a proposal; no synthetic user confirmation."""

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.async_tools import RunContinuation
from ..infrastructure.models.runtime import Conversation, RunInterrupt, RunRecord
from .memory_commands import MemoryWriteContext
from .memory_observability import after_commit, observe
from .memory_proposals import _database_now
from .memory_service import MemoryService
from .run_events import EventWriter

logger = logging.getLogger(__name__)


async def expire_confirmations(factory: SessionFactory, *, batch_size: int = 100) -> int:
    if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise ValueError("invalid confirmation expiry batch")
    async with factory() as session:
        references = (
            await session.execute(
                select(
                    RunInterrupt.id, RunInterrupt.tenant_id, RunInterrupt.run_id, RunInterrupt.conversation_id
                )
                .where(
                    RunInterrupt.interrupt_type == "MEMORY_CONFIRMATION",
                    RunInterrupt.status == "WAITING",
                    RunInterrupt.is_deleted.is_(False),
                    RunInterrupt.expires_at <= func.statement_timestamp(),
                )
                .order_by(RunInterrupt.expires_at, RunInterrupt.id)
                .limit(batch_size)
            )
        ).all()
    count = 0
    for interrupt_id, tenant, run_id, conversation_id in references:
        try:
            async with factory.begin() as session:
                changed = await _expire(session, interrupt_id, tenant, run_id, conversation_id)
            count += int(changed)
        except SQLAlchemyError as exc:
            logger.warning(
                "memory_confirmation_expiry_failed",
                extra={"proposal_id": str(interrupt_id), "error_type": type(exc).__name__},
            )
            observe("memory_confirmation_total", labels={"outcome": "ERROR"})
    return count


async def _expire(
    session: AsyncSession, interrupt_id: UUID, tenant: str, run_id: UUID, conversation_id: UUID
) -> bool:
    owned = await _locked_confirmation(session, interrupt_id, tenant, run_id, conversation_id)
    if owned is None:
        return False
    interrupt, run = owned
    await MemoryService.lock_scope(
        session,
        MemoryWriteContext(
            tenant_id=tenant, user_id=run.user_id, actor_type="SYSTEM", actor_id="memory-confirmation-expiry"
        ),
    )
    now = await _database_now(session)
    if interrupt.expires_at is None or interrupt.expires_at > now:
        return False
    text = "记忆提议已过期，未保存；可以继续对话或重新提出记忆请求。"
    payload = {
        "proposal_id": str(interrupt_id),
        "outcome": "EXPIRED",
        "saved": False,
        "text": text,
        "delta": text,
    }
    await EventWriter(session).append(
        tenant_id=tenant,
        conversation_id=conversation_id,
        run_id=run_id,
        event_type="MEMORY_CONFIRMATION_RESOLVED",
        source_event_id=interrupt_id,
        stream_type="message.delta",
        payload=payload,
    )
    interrupt.status, interrupt.resolved_at, interrupt.update_time = "EXPIRED", now, now
    interrupt.resolution_json = payload
    after_commit(session, "memory_confirmation_total", labels={"outcome": "EXPIRED"})
    return True


async def _locked_confirmation(
    session: AsyncSession, interrupt_id: UUID, tenant: str, run_id: UUID, conversation_id: UUID
) -> tuple[RunInterrupt, RunRecord] | None:
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant,
            Conversation.is_deleted.is_(False),
        )
        .with_for_update()
    )
    run = await session.scalar(
        select(RunRecord)
        .where(
            RunRecord.id == run_id,
            RunRecord.tenant_id == tenant,
            RunRecord.conversation_id == conversation_id,
            RunRecord.is_deleted.is_(False),
        )
        .with_for_update()
    )
    if run is None or conversation is None or run.user_id != conversation.user_id:
        return None
    await session.scalar(
        select(RunContinuation)
        .where(RunContinuation.tenant_id == tenant, RunContinuation.run_id == run_id)
        .with_for_update()
    )
    interrupt = await session.scalar(
        select(RunInterrupt)
        .where(
            RunInterrupt.id == interrupt_id,
            RunInterrupt.tenant_id == tenant,
            RunInterrupt.run_id == run_id,
            RunInterrupt.conversation_id == conversation_id,
        )
        .with_for_update()
    )
    if interrupt is None or interrupt.status != "WAITING" or interrupt.is_deleted:
        return None
    return interrupt, run
