"""Grant explicit intent only from a matching canonical user command.

v2 syntax: `/memory remember <predicate> <value>`. Identity (kind, cardinality,
scope) comes from the taxonomy, never from the user or the model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import MemorySourceEvidence, RememberMemoryInput
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_contracts.tasks import ContractModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import CanonicalEvent, RunRecord, UserMemory
from .async_tools.continuation_service import ExecutionIdentity, ensure_execution
from .memory_commands import MemoryWriteContext
from .memory_service import MemoryService

COMMAND_PREFIX = ("/memory", "remember")


class MemorySavedReceipt(ContractModel):
    saved: Literal[True] = True
    confirmation_required: Literal[False] = False
    memory_key: str
    version: int
    expires_at: datetime | None


def command_value(event: CanonicalEvent) -> tuple[str, str] | None:
    """Parse `<predicate> <value>` from a real inbound `/memory remember` command."""

    text = event.payload_json.get("text")
    if not isinstance(text, str):
        return None
    parts = text.strip().split(maxsplit=3)
    if len(parts) != 4 or tuple(parts[:2]) != COMMAND_PREFIX or not parts[3].strip():
        return None
    return parts[2], parts[3]


def matches_user_command(event: CanonicalEvent, candidate: RememberMemoryInput) -> bool:
    parsed = command_value(event)
    if parsed is None:
        return False
    predicate, value = parsed
    return (
        candidate.predicate == predicate
        and candidate.scope_state.value == "ABSENT"
        and dict(candidate.content) == {"value": value}
    )


async def save_user_command(
    factory: SessionFactory,
    session: AsyncSession,
    run: RunRecord,
    event: CanonicalEvent,
    identity: ExecutionIdentity,
    candidate: RememberMemoryInput,
    settings: MemoryPolicySettings,
    canaries: tuple[str, ...],
) -> MemorySavedReceipt:
    ctx = _command_context(run, event, settings, canaries)
    await MemoryService.lock_scope(session, ctx)
    ensure_execution(run, identity, await database_now(session))
    outcome = await MemoryService(factory).apply_candidate_in_session(
        session,
        ctx,
        candidate.normalized(taxonomy_version=1),
        command_id=str(uuid5(NAMESPACE_URL, f"memory.explicit:{event.id}")),
        endpoint="memory.explicit",
    )
    ensure_execution(run, identity, await database_now(session))
    row = await session.scalar(
        select(UserMemory)
        .where(
            UserMemory.tenant_id == run.tenant_id,
            UserMemory.user_id == run.user_id,
            UserMemory.memory_key == outcome.memory_key,
        )
        .execution_options(populate_existing=True)
    )
    if row is None or outcome.status == "CANDIDATE":
        raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
    return MemorySavedReceipt(memory_key=row.memory_key, version=row.version, expires_at=row.expires_at)


def _command_context(
    run: RunRecord,
    event: CanonicalEvent,
    settings: MemoryPolicySettings,
    canaries: tuple[str, ...],
) -> MemoryWriteContext:
    revision = event.payload_json.get("source_scope_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    return MemoryWriteContext(
        tenant_id=run.tenant_id,
        user_id=run.user_id,
        actor_type="USER",
        actor_id=str(run.user_id),
        settings=settings,
        credential_canaries=canaries,
        source_run_id=run.id,
        conversation_id=run.conversation_id,
        confirmation_event_id=event.id,
        evidence=MemorySourceEvidence(
            source_event_id=event.id,
            source_seq=event.seq,
            source_scope_revision=revision,
            source_quote=str(event.payload_json["text"]),
        ),
    )
