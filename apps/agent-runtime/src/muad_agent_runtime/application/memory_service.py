"""Runtime's single transactional writer; parent locks belong to the caller."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.canonical import canonical_json
from muad_contracts.memory import memory_hash
from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory, get_session_factory
from ..infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    MemoryCommandReceipt,
    MemoryScope,
    UserMemory,
)
from .memory_commands import MemoryCommand, MemoryCommandOutcome, MemoryCommandResponse, MemoryWriteContext
from .memory_mutations import MemoryBatchPlan, memory_snapshot, persist_plan, plan_mutations, source_times
from .memory_observability import after_commit, observe

logger = logging.getLogger(__name__)


def _observe_conflict(exc: AppError) -> None:
    reasons: dict[str, str] = {
        ErrorCode.MEMORY_VERSION_CONFLICT: "VERSION",
        ErrorCode.MEMORY_QUOTA_EXCEEDED: "QUOTA",
        ErrorCode.MEMORY_FORBIDDEN_CONTENT: "POLICY",
        ErrorCode.FORBIDDEN: "POLICY",
    }
    reason = reasons.get(exc.code)
    if reason is not None:
        observe("memory_write_conflicts_total", labels={"reason": reason})


ALLOWED_CATEGORIES = frozenset({"PREFERENCE", "WORK_STYLE", "EXPLICIT"})
SOURCE_USER_EXPLICIT = "USER_EXPLICIT"
SOURCE_AGENT_INFERRED = "AGENT_INFERRED"


def _observe_retirements(session: AsyncSession, plan: MemoryBatchPlan) -> None:
    evictions = sum(change.reason == "QUOTA_EVICT" for change in plan.changes)
    expired = sum(change.reason == "EXPIRE" for change in plan.changes)
    if evictions:
        after_commit(session, "memory_quota_evictions_total", amount=evictions)
    if expired:
        after_commit(session, "memory_expiry_total", labels={"outcome": "OK"}, amount=expired)


def command_fingerprint(ctx: MemoryWriteContext, command: MemoryCommand) -> str:
    """Bind authority and inputs; quota settings and secrets are never stored."""
    payload = command.model_dump(mode="json", exclude={"command_id"})
    payload.update(
        tenant_id=ctx.tenant_id,
        user_id=str(ctx.user_id),
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        source_run_id=str(ctx.source_run_id) if ctx.source_run_id else None,
        conversation_id=str(ctx.conversation_id) if ctx.conversation_id else None,
        confirmation_event_id=str(ctx.confirmation_event_id) if ctx.confirmation_event_id else None,
        evidence=ctx.evidence.model_dump(mode="json") if ctx.evidence else None,
        expected_forget_epoch=ctx.expected_forget_epoch,
    )
    # Omitted expiry applies policy; explicit null requests no expiry.
    payload["mutations"] = [
        item.model_dump(mode="json", exclude={"expires_at"})
        | ({"expires_at": item.model_dump(mode="json")["expires_at"]} if item.expiry_provided else {})
        for item in command.mutations
    ]
    return memory_hash(payload)


async def _lock_command(session: AsyncSession, ctx: MemoryWriteContext, command: MemoryCommand) -> None:
    # Receipt uniqueness also covers cross-endpoint and cross-user collisions.
    identity = canonical_json(["memory_command_receipt", ctx.tenant_id, command.command_id])
    key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(key)))


async def _receipt(
    session: AsyncSession, ctx: MemoryWriteContext, command: MemoryCommand, fingerprint: str
) -> MemoryCommandOutcome | None:
    row = (
        await session.execute(
            select(MemoryCommandReceipt).where(
                MemoryCommandReceipt.tenant_id == ctx.tenant_id,
                MemoryCommandReceipt.command_id == command.command_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.request_fingerprint != fingerprint:
        raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
    return MemoryCommandOutcome(MemoryCommandResponse.model_validate(row.response_json), replayed=True)


def _stale(scope: MemoryScope, ctx: MemoryWriteContext, command: MemoryCommand) -> bool:
    return (
        command.expected_scope_revision is not None and command.expected_scope_revision != scope.revision
    ) or (ctx.expected_forget_epoch is not None and ctx.expected_forget_epoch != scope.forget_epoch)


async def _save_receipt(
    session: AsyncSession,
    ctx: MemoryWriteContext,
    command: MemoryCommand,
    fingerprint: str,
    response: MemoryCommandResponse,
) -> MemoryCommandOutcome:
    session.add(
        MemoryCommandReceipt(
            tenant_id=ctx.tenant_id,
            command_id=command.command_id,
            endpoint=command.endpoint,
            user_id=ctx.user_id,
            request_fingerprint=fingerprint,
            status=response.status,
            response_json=response.model_dump(mode="json"),
            source_run_id=ctx.source_run_id,
        )
    )
    await session.flush()
    return MemoryCommandOutcome(response, replayed=False)


async def _validate_confirmation(
    session: AsyncSession, ctx: MemoryWriteContext, command: MemoryCommand
) -> None:
    if ctx.actor_type != "USER" or not any(item.action == "UPSERT" for item in command.mutations):
        return
    if ctx.confirmation_event_id is None:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    event = (
        await session.execute(
            select(CanonicalEvent.id)
            .join(
                Conversation,
                CanonicalEvent.conversation_id == Conversation.id,
            )
            .where(
                CanonicalEvent.id == ctx.confirmation_event_id,
                CanonicalEvent.tenant_id == ctx.tenant_id,
                CanonicalEvent.event_type == "USER_MESSAGE",
                CanonicalEvent.is_deleted.is_(False),
                Conversation.tenant_id == ctx.tenant_id,
                Conversation.user_id == ctx.user_id,
                Conversation.is_deleted.is_(False),
                *([Conversation.id == ctx.conversation_id] if ctx.conversation_id is not None else []),
            )
        )
    ).scalar_one_or_none()
    if event is None:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)


class MemoryService:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory

    def _sessions(self) -> SessionFactory:
        return self._session_factory if self._session_factory is not None else get_session_factory()

    async def execute(self, ctx: MemoryWriteContext, command: MemoryCommand) -> MemoryCommandOutcome:
        """Return success only after the complete command commits."""
        try:
            async with self._sessions().begin() as session:
                outcome = await self.execute_in_session(session, ctx, command)
            return outcome
        except SQLAlchemyError as exc:
            self._report_database_error(ctx, command, exc)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None

    async def execute_in_session(
        self, session: AsyncSession, ctx: MemoryWriteContext, command: MemoryCommand
    ) -> MemoryCommandOutcome:
        """Flush without committing; rejection rolls back the caller transaction.

        The caller owns Conversation/Run/Interrupt/Job locks and canonical effects.
        No network IO or per-item commit occurs inside this primitive.
        """
        if not session.in_transaction():
            raise RuntimeError("memory command requires a caller-owned transaction")
        try:
            return await self._execute_locked(session, ctx, command)
        except SQLAlchemyError as exc:
            await session.rollback()
            self._report_database_error(ctx, command, exc)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
        except AppError as exc:
            await session.rollback()
            observe("memory_write_total", labels={"outcome": "ERROR"})
            _observe_conflict(exc)
            raise

    @staticmethod
    def _report_database_error(ctx: MemoryWriteContext, command: MemoryCommand, exc: SQLAlchemyError) -> None:
        observe("memory_write_total", labels={"outcome": "ERROR"})
        # SQLAlchemy exception text contains bound candidate/quote values.
        logger.warning(
            "memory_command_failed",
            extra={
                "tenant_id": ctx.tenant_id,
                "user_id": str(ctx.user_id),
                "command_id": command.command_id,
                "error_code": ErrorCode.COMMON_INTERNAL_ERROR.value,
                "error_type": type(exc).__name__,
            },
        )

    @staticmethod
    async def lock_scope(session: AsyncSession, ctx: MemoryWriteContext) -> MemoryScope:
        await session.execute(
            pg_insert(MemoryScope)
            .values(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id"])
        )
        return (
            await session.execute(
                select(MemoryScope)
                .where(
                    MemoryScope.tenant_id == ctx.tenant_id,
                    MemoryScope.user_id == ctx.user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    async def _execute_locked(
        self, session: AsyncSession, ctx: MemoryWriteContext, command: MemoryCommand
    ) -> MemoryCommandOutcome:
        fingerprint = command_fingerprint(ctx, command)
        scope = await self.lock_scope(session, ctx)
        await _lock_command(session, ctx, command)
        replay = await _receipt(session, ctx, command, fingerprint)
        if replay is not None:
            return replay
        response = MemoryCommandResponse(
            command_id=command.command_id,
            scope_revision=scope.revision,
            forget_epoch=scope.forget_epoch,
            changed_count=0,
            status="STALE",
            items=[],
        )
        if not _stale(scope, ctx, command):
            response = await self._apply(session, ctx, command, scope)
        receipt = await _save_receipt(session, ctx, command, fingerprint, response)
        after_commit(session, "memory_write_total", labels={"outcome": (
            "STALE" if response.status == "STALE" else "OK" if response.changed_count else "UNCHANGED")})
        if response.status == "STALE":
            after_commit(session, "memory_write_conflicts_total", labels={"reason": "STALE"})
        return receipt

    @staticmethod
    async def _apply(
        session: AsyncSession, ctx: MemoryWriteContext, command: MemoryCommand, scope: MemoryScope
    ) -> MemoryCommandResponse:
        await _validate_confirmation(session, ctx, command)
        now: datetime = (await session.execute(select(func.statement_timestamp()))).scalar_one()
        keys = [item.key for item in command.mutations if item.key is not None]
        rows = list(
            (
                await session.scalars(
                    select(UserMemory)
                    .where(
                        UserMemory.tenant_id == ctx.tenant_id,
                        UserMemory.user_id == ctx.user_id,
                        UserMemory.is_deleted.is_(False) | UserMemory.memory_key.in_(keys),
                    )
                    .order_by(UserMemory.memory_key)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        times = await source_times(session, ctx, rows)
        plan = plan_mutations(command, ctx, rows, now, scope.revision + 1, times)
        results = await persist_plan(session, ctx, command, rows, plan, now)
        _observe_retirements(session, plan)
        if plan.changes:
            scope.revision += 1
            scope.update_time = now
            if any(change.reason in ("USER_DELETE", "ADMIN_DELETE") for change in plan.changes):
                scope.forget_epoch += 1
        return MemoryCommandResponse(
            command_id=command.command_id,
            scope_revision=scope.revision,
            forget_epoch=scope.forget_epoch,
            changed_count=len(plan.changes),
            status="APPLIED" if plan.changes else "UNCHANGED",
            items=results,
        )

    async def list_entries(self, tenant_id: str, user_id: UUID) -> list[dict[str, JsonValue]]:
        async with self._sessions()() as session:
            rows = (
                await session.scalars(
                    select(UserMemory)
                    .where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.enabled.is_(True),
                        UserMemory.is_deleted.is_(False),
                        (
                            UserMemory.expires_at.is_(None)
                            | (UserMemory.expires_at > func.statement_timestamp())
                        ),
                    )
                    .order_by(UserMemory.memory_key)
                )
            ).all()
            return [memory_snapshot(row) for row in rows]

    async def list_for_injection(
        self, tenant_id: str, user_id: UUID, *, limit: int
    ) -> list[dict[str, JsonValue]]:
        async with self._sessions()() as session:
            return await self.list_for_injection_with_session(session, tenant_id, user_id, limit=limit)

    @staticmethod
    async def list_for_injection_with_session(
        session: AsyncSession, tenant_id: str, user_id: UUID, *, limit: int
    ) -> list[dict[str, JsonValue]]:
        rows = (
            await session.scalars(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == tenant_id,
                    UserMemory.user_id == user_id,
                    UserMemory.author.in_(("USER", "ADMIN")),
                    UserMemory.category.in_(("PREFERENCE", "WORK_STYLE")),
                    UserMemory.enabled.is_(True),
                    UserMemory.is_deleted.is_(False),
                    (UserMemory.expires_at.is_(None) | (UserMemory.expires_at > func.statement_timestamp())),
                )
                .order_by(UserMemory.update_time.desc(), UserMemory.memory_key)
                .limit(limit)
            )
        ).all()
        return [memory_snapshot(row) for row in rows]

    async def search(
        self, tenant_id: str, user_id: UUID, *, prefix: str | None = None, limit: int
    ) -> list[dict[str, JsonValue]]:
        filters = [
            UserMemory.tenant_id == tenant_id,
            UserMemory.user_id == user_id,
            UserMemory.enabled.is_(True),
            UserMemory.is_deleted.is_(False),
            (UserMemory.expires_at.is_(None) | (UserMemory.expires_at > func.statement_timestamp())),
        ]
        if prefix:
            filters.append(UserMemory.memory_key.startswith(prefix, autoescape=True))
        async with self._sessions()() as session:
            rows = (
                await session.scalars(
                    select(UserMemory)
                    .where(*filters)
                    .order_by(UserMemory.update_time.desc(), UserMemory.memory_key)
                    .limit(limit)
                )
            ).all()
            return [memory_snapshot(row) for row in rows]
