"""Grant explicit intent only from a matching canonical user command."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    FactContent,
    MemoryCandidate,
    MemoryCategory,
    MemorySourceEvidence,
    RememberMemoryInput,
)
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_contracts.tasks import ContractModel
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import CanonicalEvent, RunRecord, UserMemory
from .async_tools.continuation_service import ExecutionIdentity, ensure_execution
from .memory_commands import MemoryCommand, MemoryMutation, MemoryWriteContext
from .memory_service import MemoryService


class MemorySavedReceipt(ContractModel):
    saved: Literal[True] = True
    confirmation_required: Literal[False] = False
    memory_key: str
    version: int
    expires_at: datetime | None


def matches_user_command(event: CanonicalEvent, candidate: RememberMemoryInput) -> bool:
    text = event.payload_json.get("text")
    if not isinstance(text, str):
        return False
    parts = text.strip().split(maxsplit=4)
    if len(parts) != 5 or parts[:2] != ["/memory", "remember"]:
        return False
    try:
        command = RememberMemoryInput.model_validate(
            {"memory_key": parts[2], "category": parts[3], "value": parts[4]}
        )
    except ValidationError:
        return False
    return (command.memory_key, command.category, command.value) == (
        candidate.memory_key,
        candidate.category,
        candidate.value,
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
    mutation = MemoryMutation(
        action="UPSERT",
        candidate=MemoryCandidate(
            memory_key=candidate.memory_key,
            category=MemoryCategory(candidate.category),
            content=FactContent(value=candidate.value),
        ),
    )
    command = MemoryCommand(
        command_id=str(uuid5(NAMESPACE_URL, f"memory.explicit:{event.id}")),
        endpoint="memory.explicit",
        mutations=[mutation],
    )
    result = await MemoryService(factory).execute_in_session(session, ctx, command)
    ensure_execution(run, identity, await database_now(session))
    row = await session.scalar(
        select(UserMemory).where(
            UserMemory.tenant_id == run.tenant_id,
            UserMemory.user_id == run.user_id,
            UserMemory.memory_key == candidate.memory_key,
        )
    )
    if row is None or result.response.status == "STALE":
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
