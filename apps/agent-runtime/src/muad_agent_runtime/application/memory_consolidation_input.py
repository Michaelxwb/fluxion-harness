"""Bounded, sanitised source data, never a current conversation tail."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from muad_contracts import canonical_json
from muad_contracts.memory import MemoryResumeRef
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_contracts.tasks import ContractModel
from pydantic import JsonValue, StrictStr, TypeAdapter
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import CanonicalEvent, MemoryConsolidationJob, MemoryScope, UserMemory
from .context_events import covered_up_to, summary_through
from .memory_consolidation import ConsolidationInputTooLarge, sanitize_input


class ConsolidationStale(ValueError):
    def __init__(self) -> None:
        super().__init__("STALE")


class ConsolidationText(ContractModel):
    role: Literal["USER", "ASSISTANT"]
    seq: int
    text: StrictStr


class ConsolidationInput(ContractModel):
    through_seq: int
    input_scope_revision: int
    summary_seq: int | None
    covers_up_to_seq: int
    summary: JsonValue
    messages: list[ConsolidationText]
    memory_constraints: list[dict[str, JsonValue]]
    resume_ref: MemoryResumeRef | None


async def build_input(
    session: AsyncSession,
    job: MemoryConsolidationJob,
    *,
    credential_canaries: tuple[str, ...],
    max_input_bytes: int = MemoryPolicySettings().consolidation_max_input_bytes,
) -> ConsolidationInput:
    revision = await session.scalar(
        select(MemoryScope.revision).where(
            MemoryScope.tenant_id == job.tenant_id, MemoryScope.user_id == job.user_id
        )
    )
    revision = revision or 0
    if revision != job.enqueued_scope_revision:
        raise ConsolidationStale()
    summary = await summary_through(session, job.tenant_id, job.conversation_id, job.through_seq)
    covered = covered_up_to(summary.payload_json if summary else None)
    resume = MemoryResumeRef.model_validate(job.resume_ref_json) if job.resume_ref_json else None
    constraints = await _constraints(session, job, resume, credential_canaries)
    payload = ConsolidationInput(
        through_seq=job.through_seq,
        input_scope_revision=revision,
        summary_seq=summary.seq if summary else None,
        covers_up_to_seq=covered,
        summary=sanitize_input(summary.payload_json.get("summary") if summary else None, credential_canaries),
        messages=[],
        memory_constraints=constraints,
        resume_ref=resume,
    )
    async for message in _source_texts(session, job, covered, credential_canaries):
        payload.messages.append(message)
        _check_budget(payload, max_input_bytes)
    _check_budget(payload, max_input_bytes)
    return payload


async def _source_texts(
    session: AsyncSession,
    job: MemoryConsolidationJob,
    covered: int,
    canaries: tuple[str, ...],
) -> AsyncIterator[ConsolidationText]:
    events = await session.stream_scalars(
        select(CanonicalEvent)
        .where(
            CanonicalEvent.tenant_id == job.tenant_id,
            CanonicalEvent.conversation_id == job.conversation_id,
            CanonicalEvent.seq > covered,
            CanonicalEvent.seq <= job.through_seq,
            CanonicalEvent.event_type.in_(("USER_MESSAGE", "ASSISTANT_MESSAGE")),
            CanonicalEvent.is_deleted.is_(False),
        )
        .order_by(CanonicalEvent.seq)
        .execution_options(yield_per=100)
    )
    try:
        async for row in events:
            text = row.payload_json.get("text")
            if isinstance(text, str):
                sanitized = sanitize_input(text, canaries)
                assert isinstance(sanitized, str)
                yield ConsolidationText(
                    role="USER" if row.event_type == "USER_MESSAGE" else "ASSISTANT",
                    seq=row.seq,
                    text=sanitized,
                )
    finally:
        await events.close()


def _check_budget(payload: ConsolidationInput, maximum: int) -> None:
    if len(canonical_json(payload.model_dump(mode="json")).encode("utf-8")) > maximum:
        raise ConsolidationInputTooLarge()


async def _constraints(
    session: AsyncSession,
    job: MemoryConsolidationJob,
    resume: MemoryResumeRef | None,
    canaries: tuple[str, ...],
) -> list[dict[str, JsonValue]]:
    # Active/disabled rows are capped by absolute quota. Query the selected tombstone by key;
    # arbitrary retired keys remain enforced by the transactional writer, without an unbounded scan.
    rows = await session.scalars(
        select(UserMemory)
        .where(
            UserMemory.tenant_id == job.tenant_id,
            UserMemory.user_id == job.user_id,
            or_(
                UserMemory.is_deleted.is_(False),
                UserMemory.memory_key == resume.memory_key if resume else false(),
            ),
        )
        .order_by(UserMemory.memory_key)
    )
    constraints: list[dict[str, JsonValue]] = [
        {
            "memory_key": row.memory_key,
            "version": row.version,
            "author": row.author,
            "category": row.category,
            "enabled": row.enabled,
            "is_deleted": row.is_deleted,
            "retirement_reason": row.retirement_reason,
            "retirement_revision": row.retirement_revision,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "content": row.content_json if resume and row.memory_key == resume.memory_key else None,
        }
        for row in rows
    ]
    raw: list[JsonValue] = [item for item in constraints]
    return TypeAdapter(list[dict[str, JsonValue]]).validate_python(sanitize_input(raw, canaries))
