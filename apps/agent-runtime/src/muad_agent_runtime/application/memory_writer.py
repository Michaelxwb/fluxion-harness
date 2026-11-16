"""Transactional v2 user-memory writer.

This is the v2 write path used by new callers. It keeps the existing v1
service intact until the runtime consumers are switched in TASK-005, but the
state transition is already the final v2 protocol: stable identities,
target-bound candidates, clear-only forget_epoch, idempotent receipts.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast, get_args
from uuid import UUID, uuid4

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.canonical import canonical_json
from muad_contracts.memory import MemoryAuthor, MemoryCandidate, MemoryRetirementReason
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import (
    MemoryCommandReceipt,
    MemoryConflictCandidate,
    MemoryScope,
    UserMemory,
    UserMemoryHistory,
)
from .memory_commands import MemoryWriteContext
from .memory_identity import memory_key as build_memory_key
from .memory_identity import scope_hash
from .memory_mutations import memory_state_any
from .memory_service import MemoryService
from .memory_write import MemoryWriteState, decide_automatic_update, plan_memory_capacity

MemoryWriteAction = Literal["UPSERT", "CREATE", "DELETE", "CLEAR"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryWriteCommand:
    command_id: str
    endpoint: str
    action: MemoryWriteAction
    candidate: MemoryCandidate | None = None
    memory_id: UUID | None = None
    expected_version: int | None = None
    note_id: UUID | None = None


MemoryOutcomeStatus = Literal[
    "CREATED",
    "UPDATED",
    "CANDIDATE",
    "DELETED",
    "CLEARED",
    "ACCEPTED",
    "REJECTED",
    "PATCHED",
    "RESTORED",
    "SKIPPED",
    "UNCHANGED",
]


@dataclass(frozen=True, slots=True)
class MemoryWriteOutcome:
    status: MemoryOutcomeStatus
    memory_key: str | None
    version: int | None
    candidate_id: UUID | None = None
    replayed: bool = False


def _fingerprint(ctx: MemoryWriteContext, command: MemoryWriteCommand) -> str:
    payload = {
        "tenant": ctx.tenant_id,
        "user": str(ctx.user_id),
        "actor": ctx.actor_type,
        "action": command.action,
        "candidate": command.candidate.model_dump(mode="json") if command.candidate else None,
        "memory_id": str(command.memory_id) if command.memory_id else None,
        "expected_version": command.expected_version,
    }
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _authority(ctx: MemoryWriteContext) -> str:
    return {
        "USER": "USER_EXPLICIT",
        "ADMIN": "ADMIN_EDIT",
        "AGENT": "AGENT_INFERRED",
        "SYSTEM": "AGENT_INFERRED",
    }[ctx.actor_type]


def _author(ctx: MemoryWriteContext) -> str:
    return "AGENT" if ctx.actor_type in ("AGENT", "SYSTEM") else ctx.actor_type


def _candidate_hash(candidate: MemoryCandidate) -> str:
    return hashlib.sha256(canonical_json(candidate.content).encode("utf-8")).hexdigest()


def _after(row: UserMemory) -> dict[str, object]:
    return {
        "memory_key": row.memory_key,
        "memory_kind": row.memory_kind,
        "predicate": row.predicate,
        "scope_state": row.scope_state,
        "scope": row.scope_json,
        "content": row.content_json,
        "title": row.title,
        "summary": row.summary,
        "tags": row.tags_json,
        "version": row.version,
        "author": row.author,
        "authority": row.authority,
        "is_deleted": row.is_deleted,
    }


def _write_decision(
    row: UserMemory | None, candidate: MemoryCandidate, ctx: MemoryWriteContext
) -> Literal["APPLY", "PENDING", "SKIPPED"]:
    """Automatic writes never resurrect tombstones, re-enable rows or overwrite a live value.

    Same content from an independent evidence event still bumps the version; a different
    inferred value on a live slot only creates a PENDING candidate (design §3.5.1 step 6).
    """

    if _author(ctx) != "AGENT":
        return "APPLY"
    if row is None:
        return "APPLY" if ctx.evidence is not None else "SKIPPED"
    same = row.content_json == dict(candidate.content)
    if _stale_source(row, ctx):
        return "SKIPPED"
    decision = decide_automatic_update(
        memory_state_any(row),
        expected_version=row.version,
        evidence=ctx.evidence,
        source_run_id=ctx.source_run_id or UUID(int=0),
        same_conversation=row.source_ref == f"conversation:{ctx.conversation_id}",
    )
    if decision.reason == "OLD_SOURCE":
        return "SKIPPED"
    if row.is_deleted:
        return "APPLY" if decision.allowed else "SKIPPED"
    if not row.enabled:
        return "SKIPPED"
    return "APPLY" if same else "PENDING"


def _stale_source(row: UserMemory, ctx: MemoryWriteContext) -> bool:
    """A source no newer than what the slot already recorded cannot change or propose anything."""

    evidence = ctx.evidence
    if evidence is None or row.last_source_seq is None:
        return False
    same_conversation = row.source_ref == f"conversation:{ctx.conversation_id}"
    same_run = row.last_source_run_id == ctx.source_run_id
    return (same_conversation or same_run) and evidence.source_seq <= row.last_source_seq


def _expires_at(candidate: MemoryCandidate, ctx: MemoryWriteContext, now: datetime) -> datetime | None:
    """Explicit expiry wins; otherwise the frozen policy TTL for the writing author applies."""

    if candidate.expires_at is not None:
        if candidate.expires_at <= now:
            raise AppError(ErrorCode.MEMORY_VALUE_INVALID)
        return candidate.expires_at
    author = _author(ctx)
    days = ctx.settings.ttl_days_user if author in ("USER", "ADMIN") else ctx.settings.ttl_days_agent
    if days is None or days <= 0:
        if author == "AGENT":
            raise AppError(ErrorCode.MEMORY_VALUE_INVALID)
        return None
    return now + timedelta(days=days)


def _apply_candidate(
    row: UserMemory, candidate: MemoryCandidate, ctx: MemoryWriteContext, now: datetime
) -> None:
    row.memory_kind = candidate.memory_kind
    row.predicate = candidate.predicate
    row.scope_state = candidate.scope_state.value
    row.scope_json = dict(candidate.scope)
    row.scope_hash = scope_hash(candidate.scope)
    row.content_json = dict(candidate.content)
    row.content_text = canonical_json(candidate.content)
    row.title = candidate.title
    row.summary = candidate.summary
    row.tags_json = list(candidate.tags)
    row.cardinality = candidate.cardinality.value
    row.priority = candidate.priority.value
    row.taxonomy_version = candidate.taxonomy_version
    row.author = _author(ctx)
    row.authority = _authority(ctx)
    row.explicit_request = ctx.actor_type == "USER"
    row.confirmation_event_id = ctx.confirmation_event_id
    # Provenance and confirmation counters stay as the v1 writer defined them:
    # B-15 / RULE-memory-001 assert the saved row is bound to its real source.
    row.confirmed_count = (row.confirmed_count or 0) + (1 if ctx.actor_type == "USER" else 0)
    if ctx.actor_type == "USER":
        row.last_confirmed_at = now
    evidence = ctx.evidence
    row.source_event_id = evidence.source_event_id if evidence is not None else None
    row.source_seq = evidence.source_seq if evidence is not None else None
    row.source_quote = evidence.source_quote if evidence is not None else None
    row.source_ref = f"conversation:{ctx.conversation_id}" if ctx.conversation_id else None
    row.last_source_run_id = ctx.source_run_id
    row.last_source_seq = evidence.source_seq if evidence is not None else None
    row.expires_at = _expires_at(candidate, ctx, now)
    row.enabled = True
    row.is_deleted = False
    row.retirement_reason = None
    row.retirement_revision = None
    row.update_time = now


async def _history(
    session: AsyncSession,
    row: UserMemory,
    ctx: MemoryWriteContext,
    command: MemoryWriteCommand,
    reason: str,
    *,
    now: datetime,
) -> None:
    session.add(
        UserMemoryHistory(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            memory_key=row.memory_key,
            origin_id=row.id,
            version=row.version,
            change_reason=reason,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            command_id=command.command_id,
            source_run_id=ctx.source_run_id,
            after_json=_after(row),
        )
    )
    row.update_time = now


async def _receipt(
    session: AsyncSession,
    ctx: MemoryWriteContext,
    command: MemoryWriteCommand,
    fingerprint: str,
) -> MemoryWriteOutcome | None:
    receipt = await session.scalar(
        select(MemoryCommandReceipt).where(
            MemoryCommandReceipt.tenant_id == ctx.tenant_id,
            MemoryCommandReceipt.command_id == command.command_id,
        )
    )
    if receipt is None:
        return None
    if receipt.request_fingerprint != fingerprint:
        raise AppError(ErrorCode.MEMORY_IDEMPOTENCY_CONFLICT)
    payload = receipt.response_json
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    status = payload.get("status")
    memory_key = payload.get("memory_key")
    version = payload.get("version")
    candidate_id = payload.get("candidate_id")
    if not isinstance(status, str) or status not in get_args(MemoryOutcomeStatus):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return MemoryWriteOutcome(
        status=cast(MemoryOutcomeStatus, status),
        memory_key=memory_key if isinstance(memory_key, str) else None,
        version=version if isinstance(version, int) and not isinstance(version, bool) else None,
        candidate_id=UUID(candidate_id) if isinstance(candidate_id, str) else None,
        replayed=True,
    )


async def _save_receipt(
    session: AsyncSession,
    ctx: MemoryWriteContext,
    command: MemoryWriteCommand,
    fingerprint: str,
    outcome: MemoryWriteOutcome,
) -> None:
    session.add(
        MemoryCommandReceipt(
            tenant_id=ctx.tenant_id,
            command_id=command.command_id,
            endpoint=command.endpoint,
            user_id=ctx.user_id,
            request_fingerprint=fingerprint,
            status="APPLIED",
            response_json={
                "status": outcome.status,
                "memory_key": outcome.memory_key,
                "version": outcome.version,
                "candidate_id": str(outcome.candidate_id) if outcome.candidate_id else None,
            },
            source_run_id=ctx.source_run_id,
        )
    )


async def _pending_candidate(
    session: AsyncSession,
    ctx: MemoryWriteContext,
    row: UserMemory,
    candidate: MemoryCandidate,
    scope: MemoryScope,
    command: MemoryWriteCommand,
) -> MemoryWriteOutcome:
    content_hash = _candidate_hash(candidate)[:64]
    existing = await session.scalar(
        select(MemoryConflictCandidate).where(
            MemoryConflictCandidate.tenant_id == ctx.tenant_id,
            MemoryConflictCandidate.user_id == ctx.user_id,
            MemoryConflictCandidate.memory_key == row.memory_key,
            MemoryConflictCandidate.candidate_hash == content_hash,
            MemoryConflictCandidate.status == "PENDING",
            MemoryConflictCandidate.is_deleted.is_(False),
        )
    )
    if existing is not None:
        existing.evidence_count += 1
        existing.last_source_ref = f"run:{ctx.source_run_id}" if ctx.source_run_id else None
        return MemoryWriteOutcome("CANDIDATE", row.memory_key, row.version, existing.id, True)
    proposed = MemoryConflictCandidate(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        memory_key=row.memory_key,
        predicate=candidate.predicate,
        scope_state=candidate.scope_state.value,
        scope_json=dict(candidate.scope),
        scope_hash=scope_hash(candidate.scope),
        content_json=dict(candidate.content),
        content_hash=content_hash,
        content_text=canonical_json(candidate.content),
        title=candidate.title,
        summary=candidate.summary,
        source_quote=candidate.source_quote,
        confidence=2,
        base_version=row.version,
        base_scope_revision=scope.revision,
        forget_epoch=scope.forget_epoch,
        evidence_count=1,
        last_source_ref=f"run:{ctx.source_run_id}" if ctx.source_run_id else None,
        candidate_hash=content_hash,
        status="PENDING",
        reason="VALUE_CONFLICT",
    )
    session.add(proposed)
    await session.flush()
    return MemoryWriteOutcome("CANDIDATE", row.memory_key, row.version, proposed.id)


class MemoryWriter:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory

    def _sessions(self) -> SessionFactory:
        if self._session_factory is not None:
            return self._session_factory
        from ..infrastructure.db import get_session_factory

        return get_session_factory()

    async def execute(self, ctx: MemoryWriteContext, command: MemoryWriteCommand) -> MemoryWriteOutcome:
        try:
            async with self._sessions().begin() as session:
                return await self.execute_in_session(session, ctx, command)
        except SQLAlchemyError as exc:
            logger.warning(
                "memory_v2_write_failed",
                extra={
                    "tenant_id": ctx.tenant_id,
                    "endpoint": command.endpoint,
                    "error_type": type(exc).__name__,
                },
            )
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None

    async def execute_in_session(
        self, session: AsyncSession, ctx: MemoryWriteContext, command: MemoryWriteCommand
    ) -> MemoryWriteOutcome:
        try:
            return await self._execute_in_session(session, ctx, command)
        except SQLAlchemyError as exc:
            logger.warning(
                "memory_v2_write_failed",
                extra={
                    "tenant_id": ctx.tenant_id,
                    "endpoint": command.endpoint,
                    "error_type": type(exc).__name__,
                },
            )
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None

    async def _execute_in_session(
        self, session: AsyncSession, ctx: MemoryWriteContext, command: MemoryWriteCommand
    ) -> MemoryWriteOutcome:
        fingerprint = _fingerprint(ctx, command)
        replay = await _receipt(session, ctx, command, fingerprint)
        if replay is not None:
            return replay
        scope = await MemoryService.lock_scope(session, ctx)
        now = await database_now(session)
        if command.action == "CLEAR":
            return await self._clear(session, ctx, command, scope, now, fingerprint)
        if command.action == "DELETE":
            return await self._delete(session, ctx, command, scope, now, fingerprint)
        candidate = command.candidate
        if candidate is None:
            raise ValueError("UPSERT requires a candidate")
        note_id = command.note_id or (uuid4() if candidate.memory_kind == "NOTE" else None)
        key = build_memory_key(candidate, note_id=note_id)
        row = await session.scalar(
            select(UserMemory)
            .where(
                UserMemory.tenant_id == ctx.tenant_id,
                UserMemory.user_id == ctx.user_id,
                UserMemory.memory_key == key,
            )
            .with_for_update()
        )
        if row is not None and command.action == "CREATE":
            # Creating an existing identity (including a tombstone) is a conflict;
            # the caller must PATCH the memory or RESTORE the tombstone.
            raise AppError(ErrorCode.MEMORY_ALREADY_EXISTS)
        decision = _write_decision(row, candidate, ctx)
        if decision == "SKIPPED":
            outcome = MemoryWriteOutcome("SKIPPED", key, row.version if row is not None else None)
        elif decision == "PENDING":
            # PENDING 只针对已存在的目标槽位：decision 由 row 推出，这里收窄类型。
            assert row is not None
            outcome = await self._candidate(session, ctx, row, candidate, scope, command)
        else:
            await self._enforce_capacity(session, ctx, candidate, command, key, now, scope)
            if row is None:
                row = UserMemory(
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.user_id,
                    memory_key=key,
                    content_json=dict(candidate.content),
                    author="AGENT",
                    authority="AGENT_INFERRED",
                    version=1,
                    enabled=True,
                )
                _apply_candidate(row, candidate, ctx, now)
                row.version = 1
                session.add(row)
                await session.flush()
                outcome = MemoryWriteOutcome("CREATED", key, 1)
                scope.revision += 1
                await _history(session, row, ctx, command, "CREATE", now=now)
            else:
                row.version += 1
                _apply_candidate(row, candidate, ctx, now)
                outcome = MemoryWriteOutcome("UPDATED", key, row.version)
                scope.revision += 1
                # 用户确认的更新记 CONFIRM（管理面 patch 与 Agent 自动更新仍是 OVERWRITE）。
                await _history(
                    session,
                    row,
                    ctx,
                    command,
                    "CONFIRM" if ctx.actor_type == "USER" else "OVERWRITE",
                    now=now,
                )
        await _save_receipt(session, ctx, command, fingerprint, outcome)
        return outcome

    async def _enforce_capacity(
        self,
        session: AsyncSession,
        ctx: MemoryWriteContext,
        candidate: MemoryCandidate,
        command: MemoryWriteCommand,
        target_key: str,
        now: datetime,
        scope: MemoryScope,
    ) -> None:
        """Frozen per-user entry/byte limits plus AGENT-only eviction, applied before the write."""

        rows = (
            await session.scalars(
                select(UserMemory).where(
                    UserMemory.tenant_id == ctx.tenant_id,
                    UserMemory.user_id == ctx.user_id,
                )
            )
        ).all()
        indexed = {row.memory_key: row for row in rows}
        target = indexed.get(target_key)
        proposed = MemoryWriteState(
            memory_key=target_key,
            category=None,
            content=dict(candidate.content),
            author=MemoryAuthor(_author(ctx)),
            version=(target.version + 1) if target is not None else 1,
            updated_at=now,
            source_quote=candidate.source_quote,
            expires_at=_expires_at(candidate, ctx, now),
            confirmed_count=(target.confirmed_count if target is not None else 0)
            + (1 if ctx.actor_type == "USER" else 0),
        )
        capacity = plan_memory_capacity(
            [memory_state_any(row) for row in rows], [proposed], now=now, settings=ctx.settings
        )
        for reason, keys in (
            (MemoryRetirementReason.EXPIRE, capacity.expired_keys),
            (MemoryRetirementReason.QUOTA_EVICT, capacity.evicted_keys),
        ):
            for key in keys:
                row = indexed.get(key)
                if row is None or key == target_key:
                    continue
                row.is_deleted = True
                row.retirement_reason = reason.value
                row.retirement_revision = scope.revision
                row.version += 1
                scope.revision += 1
                await _history(session, row, ctx, command, reason.value, now=now)

    async def _candidate(
        self,
        session: AsyncSession,
        ctx: MemoryWriteContext,
        row: UserMemory,
        candidate: MemoryCandidate,
        scope: MemoryScope,
        command: MemoryWriteCommand,
    ) -> MemoryWriteOutcome:
        return await _pending_candidate(session, ctx, row, candidate, scope, command)

    async def _delete(
        self,
        session: AsyncSession,
        ctx: MemoryWriteContext,
        command: MemoryWriteCommand,
        scope: MemoryScope,
        now: datetime,
        fingerprint: str,
    ) -> MemoryWriteOutcome:
        row = await session.scalar(
            select(UserMemory)
            .where(
                UserMemory.tenant_id == ctx.tenant_id,
                UserMemory.user_id == ctx.user_id,
                UserMemory.id == command.memory_id,
            )
            .with_for_update()
        )
        if row is None or row.is_deleted:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND)
        if command.expected_version is not None and row.version != command.expected_version:
            raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
        row.is_deleted = True
        row.retirement_reason = "USER_DELETE" if ctx.actor_type == "USER" else "ADMIN_DELETE"
        row.version += 1
        row.update_time = now
        scope.revision += 1
        await self._revoke_pending(session, ctx, row.memory_key)
        await _history(session, row, ctx, command, row.retirement_reason, now=now)
        outcome = MemoryWriteOutcome("DELETED", row.memory_key, row.version)
        await _save_receipt(session, ctx, command, fingerprint, outcome)
        return outcome

    async def _clear(
        self,
        session: AsyncSession,
        ctx: MemoryWriteContext,
        command: MemoryWriteCommand,
        scope: MemoryScope,
        now: datetime,
        fingerprint: str,
    ) -> MemoryWriteOutcome:
        rows = list(
            await session.scalars(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == ctx.tenant_id,
                    UserMemory.user_id == ctx.user_id,
                    UserMemory.is_deleted.is_(False),
                )
                .with_for_update()
            )
        )
        for row in rows:
            row.is_deleted = True
            row.retirement_reason = "USER_DELETE" if ctx.actor_type == "USER" else "ADMIN_DELETE"
            row.version += 1
            row.update_time = now
            await _history(session, row, ctx, command, row.retirement_reason, now=now)
        await self._revoke_pending(session, ctx, None)
        scope.revision += 1
        scope.forget_epoch += 1
        outcome = MemoryWriteOutcome("CLEARED", None, None)
        await _save_receipt(session, ctx, command, fingerprint, outcome)
        return outcome

    async def _revoke_pending(
        self, session: AsyncSession, ctx: MemoryWriteContext, memory_key: str | None
    ) -> None:
        query = select(MemoryConflictCandidate).where(
            MemoryConflictCandidate.tenant_id == ctx.tenant_id,
            MemoryConflictCandidate.user_id == ctx.user_id,
            MemoryConflictCandidate.status == "PENDING",
            MemoryConflictCandidate.is_deleted.is_(False),
        )
        if memory_key is not None:
            query = query.where(MemoryConflictCandidate.memory_key == memory_key)
        for candidate in await session.scalars(query.with_for_update()):
            candidate.status = "STALE"

    async def set_enabled(
        self, ctx: MemoryWriteContext, memory_id: UUID, *, expected_version: int, enabled: bool
    ) -> MemoryWriteOutcome:
        async with self._sessions().begin() as session:
            scope = await MemoryService.lock_scope(session, ctx)
            now = await database_now(session)
            row = await session.scalar(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == ctx.tenant_id,
                    UserMemory.user_id == ctx.user_id,
                    UserMemory.id == memory_id,
                )
                .with_for_update()
            )
            if row is None or row.is_deleted:
                raise AppError(ErrorCode.MEMORY_NOT_FOUND)
            if row.version != expected_version:
                raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
            row.enabled = enabled
            row.version += 1
            row.update_time = now
            scope.revision += 1
            command = MemoryWriteCommand("enable", "memory.enable", "UPSERT")
            await _history(session, row, ctx, command, "ENABLE" if enabled else "DISABLE", now=now)
            return MemoryWriteOutcome("PATCHED", row.memory_key, row.version)

    async def patch(
        self, ctx: MemoryWriteContext, memory_id: UUID, *, expected_version: int, updates: dict[str, object]
    ) -> MemoryWriteOutcome:
        identity_fields = {"memory_kind", "predicate", "scope", "scope_state", "cardinality"}
        if identity_fields & set(updates):
            raise AppError(ErrorCode.MEMORY_IDENTITY_IMMUTABLE)
        async with self._sessions().begin() as session:
            scope = await MemoryService.lock_scope(session, ctx)
            now = await database_now(session)
            row = await session.scalar(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == ctx.tenant_id,
                    UserMemory.user_id == ctx.user_id,
                    UserMemory.id == memory_id,
                )
                .with_for_update()
            )
            if row is None or row.is_deleted:
                raise AppError(ErrorCode.MEMORY_NOT_FOUND)
            if row.version != expected_version:
                raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
            if row.cardinality == "SET" and "content" in updates:
                raise AppError(ErrorCode.MEMORY_IDENTITY_IMMUTABLE)
            allowed = {"content", "title", "summary", "tags", "expires_at"}
            if set(updates) - allowed:
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            if "content" in updates:
                content = updates["content"]
                if not isinstance(content, dict):
                    raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
                row.content_json = dict(content)
                row.content_text = canonical_json(row.content_json)
            if "title" in updates:
                row.title = str(updates["title"])
            if "summary" in updates:
                row.summary = str(updates["summary"])
            if "tags" in updates:
                tags = updates["tags"]
                if not isinstance(tags, list):
                    raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
                row.tags_json = [str(tag) for tag in tags]
            if "expires_at" in updates:
                expires_at = updates["expires_at"]
                if expires_at is not None and not isinstance(expires_at, datetime):
                    raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
                row.expires_at = expires_at
            row.version += 1
            row.update_time = now
            scope.revision += 1
            command = MemoryWriteCommand("patch", "memory.patch", "UPSERT")
            await _history(session, row, ctx, command, "OVERWRITE", now=now)
            return MemoryWriteOutcome("PATCHED", row.memory_key, row.version)

    async def restore(
        self, ctx: MemoryWriteContext, memory_id: UUID, *, expected_version: int
    ) -> MemoryWriteOutcome:
        async with self._sessions().begin() as session:
            scope = await MemoryService.lock_scope(session, ctx)
            now = await database_now(session)
            row = await session.scalar(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == ctx.tenant_id,
                    UserMemory.user_id == ctx.user_id,
                    UserMemory.id == memory_id,
                )
                .with_for_update()
            )
            if row is None or not row.is_deleted:
                raise AppError(ErrorCode.MEMORY_NOT_FOUND)
            if row.version != expected_version:
                raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
            row.is_deleted = False
            row.retirement_reason = None
            row.retirement_revision = None
            row.version += 1
            row.update_time = now
            scope.revision += 1
            command = MemoryWriteCommand("restore", "memory.restore", "UPSERT")
            await _history(session, row, ctx, command, "RESTORE", now=now)
            return MemoryWriteOutcome("RESTORED", row.memory_key, row.version)

    async def reject_candidate(
        self, ctx: MemoryWriteContext, candidate_id: UUID, *, expected_version: int
    ) -> MemoryWriteOutcome:
        async with self._sessions().begin() as session:
            scope = await MemoryService.lock_scope(session, ctx)
            now = await database_now(session)
            candidate = await session.scalar(
                select(MemoryConflictCandidate)
                .where(
                    MemoryConflictCandidate.tenant_id == ctx.tenant_id,
                    MemoryConflictCandidate.user_id == ctx.user_id,
                    MemoryConflictCandidate.id == candidate_id,
                    MemoryConflictCandidate.status == "PENDING",
                    MemoryConflictCandidate.is_deleted.is_(False),
                )
                .with_for_update()
            )
            if candidate is None:
                raise AppError(ErrorCode.MEMORY_CANDIDATE_NOT_FOUND)
            if candidate.base_version != expected_version:
                raise AppError(ErrorCode.MEMORY_CANDIDATE_STALE)
            candidate.status = "REJECTED"
            candidate.resolved_by = ctx.actor_id
            candidate.resolved_at = now
            candidate.update_time = now
            scope.revision += 1
            return MemoryWriteOutcome("REJECTED", candidate.memory_key, None, candidate.id)

    async def accept_candidate(
        self, ctx: MemoryWriteContext, candidate_id: UUID, *, expected_version: int
    ) -> MemoryWriteOutcome:
        async with self._sessions().begin() as session:
            scope = await MemoryService.lock_scope(session, ctx)
            now = await database_now(session)
            candidate = await session.scalar(
                select(MemoryConflictCandidate)
                .where(
                    MemoryConflictCandidate.tenant_id == ctx.tenant_id,
                    MemoryConflictCandidate.user_id == ctx.user_id,
                    MemoryConflictCandidate.id == candidate_id,
                    MemoryConflictCandidate.status == "PENDING",
                    MemoryConflictCandidate.is_deleted.is_(False),
                )
                .with_for_update()
            )
            if candidate is None:
                raise AppError(ErrorCode.MEMORY_CANDIDATE_NOT_FOUND)
            row = await session.scalar(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == ctx.tenant_id,
                    UserMemory.user_id == ctx.user_id,
                    UserMemory.memory_key == candidate.memory_key,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.version != candidate.base_version
                or row.version != expected_version
                or scope.forget_epoch != candidate.forget_epoch
            ):
                raise AppError(ErrorCode.MEMORY_CANDIDATE_STALE)
            row.memory_kind = "NOTE" if row.memory_key.startswith("v1.note.") else row.memory_kind
            row.content_json = dict(candidate.content_json)
            row.content_text = candidate.content_text
            row.title = candidate.title
            row.summary = candidate.summary
            row.version += 1
            row.author = _author(ctx)
            row.authority = _authority(ctx)
            row.explicit_request = ctx.actor_type == "USER"
            row.confirmation_event_id = ctx.confirmation_event_id
            row.update_time = now
            candidate.status = "ACCEPTED"
            candidate.resolved_by = ctx.actor_id
            candidate.resolved_at = now
            scope.revision += 1
            command = MemoryWriteCommand(
                command_id=f"accept:{candidate_id}",
                endpoint="candidate.resolve",
                action="UPSERT",
            )
            await _history(session, row, ctx, command, "CONFIRM", now=now)
            return MemoryWriteOutcome("ACCEPTED", row.memory_key, row.version)
