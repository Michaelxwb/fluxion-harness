"""Append-only canonical options and their one final outcome, scoped to the owned conversation."""

from dataclasses import dataclass
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import MemoryResumeOptions, MemoryResumeRef
from muad_logging import get_logger
from sqlalchemy import exists, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import CanonicalEvent, Conversation, RunRecord
from ..metrics import MEMORY_RESUME_METRIC, record_counter
from .memory_resume_options import options_prompt, selected_prompt

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryResumeDecision:
    reference: MemoryResumeRef | None = None
    hint: str | None = None


async def owned_conversation(session: AsyncSession, run: RunRecord, *, lock: bool = False) -> None:
    statement = select(Conversation.id).where(
        Conversation.id == run.conversation_id,
        Conversation.tenant_id == run.tenant_id,
        Conversation.user_id == run.user_id,
        Conversation.is_deleted.is_(False),
    )
    if lock:
        statement = statement.with_for_update()
    if await session.scalar(statement) is None:
        raise AppError(ErrorCode.FORBIDDEN)


async def pending_options(session: AsyncSession, run: RunRecord) -> CanonicalEvent | None:
    consumed = aliased(CanonicalEvent)
    row: CanonicalEvent | None = await session.scalar(
        select(CanonicalEvent)
        .where(
            CanonicalEvent.tenant_id == run.tenant_id,
            CanonicalEvent.conversation_id == run.conversation_id,
            CanonicalEvent.event_type == "MEMORY_RESUME_OPTIONS",
            CanonicalEvent.is_deleted.is_(False),
            ~exists(
                select(consumed.id).where(
                    consumed.tenant_id == run.tenant_id,
                    consumed.source_event_id == CanonicalEvent.id,
                    consumed.is_deleted.is_(False),
                )
            ),
        )
        .order_by(CanonicalEvent.seq.desc())
        .limit(1)
    )
    return row


async def resume_decision(
    factory: SessionFactory, run: RunRecord, *, submission_id: UUID
) -> MemoryResumeDecision:
    try:
        rows = await _resume_events(factory, run)
        return _decision(rows, submission_id)
    except (SQLAlchemyError, ValueError) as exc:
        record_counter(MEMORY_RESUME_METRIC, labels={"outcome": "ERROR"})
        logger.warning(
            "memory_resume_reference_failed",
            extra={"tenant_id": run.tenant_id, "run_id": str(run.id), "error_type": type(exc).__name__},
        )
        return MemoryResumeDecision()


async def _resume_events(factory: SessionFactory, run: RunRecord) -> list[CanonicalEvent]:
    async with factory() as session:
        await owned_conversation(session, run)
        rows = list(
            await session.scalars(
                select(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == run.tenant_id,
                    CanonicalEvent.conversation_id == run.conversation_id,
                    CanonicalEvent.run_id == run.id,
                    CanonicalEvent.is_deleted.is_(False),
                    CanonicalEvent.event_type.in_(
                        ("MEMORY_RESUME_OPTIONS", "MEMORY_RESUME_RESOLVED")
                    ),
                )
                .order_by(CanonicalEvent.seq.desc())
            )
        )
    return rows


def _selected_hint(
    rows: list[CanonicalEvent], reference: MemoryResumeRef
) -> str | None:
    for row in rows:
        if row.id != reference.options_event_id:
            continue
        if row.event_type != "MEMORY_RESUME_OPTIONS":
            return None
        options = MemoryResumeOptions.model_validate(
            {key: value for key, value in row.payload_json.items() if key != "delta"}
        )
        option = next(
            (item for item in options.items if item.memory_key == reference.memory_key),
            None,
        )
        return selected_prompt(option) if option is not None else None
    return None


def _decision(rows: list[CanonicalEvent], submission_id: UUID | None) -> MemoryResumeDecision:
    for row in rows:
        if submission_id is not None and row.submission_id != submission_id:
            continue
        if row.event_type == "MEMORY_RESUME_RESOLVED":
            if row.payload_json.get("outcome") == "SELECTED":
                reference = MemoryResumeRef.model_validate(row.payload_json.get("resume_ref"))
                if (
                    reference.resolution_event_id != row.id
                    or reference.options_event_id != row.source_event_id
                ):
                    raise ValueError("resume reference does not match its canonical outcome")
                return MemoryResumeDecision(
                    reference=reference, hint=_selected_hint(rows, reference)
                )
            return MemoryResumeDecision()
        if row.event_type == "MEMORY_RESUME_OPTIONS":
            if submission_id is None:
                # 入队只关心最终选择；未消费的候选不能遮住更早的 SELECTED。
                continue
            options = MemoryResumeOptions.model_validate(
                {key: value for key, value in row.payload_json.items() if key != "delta"}
            )
            return MemoryResumeDecision(hint=options_prompt(options))
    return MemoryResumeDecision()
