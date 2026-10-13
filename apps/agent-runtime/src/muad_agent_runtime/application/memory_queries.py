"""Complete candidate reads: rows, revision and current time share one SQL snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from muad_agent_core.memory.rank import MemoryRecord
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    MEMORY_MAX_BYTES,
    MEMORY_MAX_RECORDS,
    MemoryAuthor,
    MemoryCandidate,
    memory_content_bytes,
)
from sqlalchemy import Select, func, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import FromClause

from ..infrastructure.db import SessionFactory, get_session_factory
from ..infrastructure.models.runtime import MemoryScope, UserMemory


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryReadScope:
    tenant_id: str
    user_id: UUID

    def __post_init__(self) -> None:
        if not self.tenant_id or len(self.tenant_id) > 64:
            raise ValueError("invalid memory read scope")


@dataclass(frozen=True, slots=True)
class MemoryScopeVersion:
    revision: int
    forget_epoch: int
    as_of: datetime


@dataclass(frozen=True, slots=True)
class MemoryCandidateRow:
    id: UUID
    record: MemoryRecord
    source_quote: str | None


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    scope: MemoryReadScope
    revision: int
    forget_epoch: int
    as_of: datetime
    rows: tuple[MemoryCandidateRow, ...]

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(row.record for row in self.rows)


def _scope_source(scope: MemoryReadScope) -> FromClause:
    # A one-row anchor preserves empty users without inserting a scope row.
    anchor = select(literal(1).label("anchor")).subquery("memory_read_anchor")
    version = (
        select(MemoryScope.revision, MemoryScope.forget_epoch)
        .where(
            MemoryScope.tenant_id == scope.tenant_id,
            MemoryScope.user_id == scope.user_id,
            MemoryScope.is_deleted.is_(False),
        )
        .subquery("memory_read_scope")
    )
    return anchor.outerjoin(version, true())


def _head_statement(scope: MemoryReadScope) -> Select[tuple[int, int, datetime]]:
    source = _scope_source(scope)
    return select(
        func.coalesce(source.c.memory_read_scope_revision, 0).label("scope_revision"),
        func.coalesce(source.c.memory_read_scope_forget_epoch, 0).label("forget_epoch"),
        func.statement_timestamp().label("as_of"),
    ).select_from(source)


def _snapshot_statement(scope: MemoryReadScope) -> Select[tuple[int, int, datetime, UserMemory]]:
    head = _head_statement(scope)
    source = head.get_final_froms()[0]
    live = (
        (UserMemory.tenant_id == scope.tenant_id)
        & (UserMemory.user_id == scope.user_id)
        & UserMemory.enabled.is_(True)
        & UserMemory.is_deleted.is_(False)
        & (UserMemory.expires_at.is_(None) | (UserMemory.expires_at > func.statement_timestamp()))
    )
    return (
        select(*head.selected_columns, UserMemory)
        .select_from(
            source.outerjoin(UserMemory.__table__, live),
        )
        .order_by(UserMemory.memory_key)
    )


def _candidate(row: UserMemory) -> MemoryCandidateRow:
    candidate = MemoryCandidate.model_validate(
        {
            "memory_key": row.memory_key,
            "category": row.category,
            "content": row.content_json,
        }
    )
    record = MemoryRecord(
        memory_key=candidate.memory_key,
        content=candidate.content,
        author=MemoryAuthor(row.author),
        category=candidate.category,
        version=row.version,
        updated_at=row.update_time,
        last_confirmed_at=row.last_confirmed_at,
        expires_at=row.expires_at,
        enabled=row.enabled,
        is_deleted=row.is_deleted,
        explicit_request=row.explicit_request,
    )
    return MemoryCandidateRow(row.id, record, row.source_quote)


def _validate_bounds(rows: tuple[MemoryCandidateRow, ...]) -> None:
    if (
        len(rows) > MEMORY_MAX_RECORDS
        or sum(
            memory_content_bytes(row.record.content.model_dump(mode="json"), row.source_quote) for row in rows
        )
        > MEMORY_MAX_BYTES
    ):
        raise AppError(ErrorCode.MEMORY_READ_FAILED)


class MemoryQueries:
    """Each call releases its transaction; no latest-N or frozen-quota filtering."""

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory

    def _sessions(self) -> SessionFactory:
        return self._session_factory if self._session_factory is not None else get_session_factory()

    async def read_scope_version(self, scope: MemoryReadScope) -> MemoryScopeVersion:
        async with self._sessions()() as session:
            revision, epoch, as_of = (await session.execute(_head_statement(scope))).tuples().one()
        return MemoryScopeVersion(revision, epoch, as_of)

    async def read_snapshot(self, scope: MemoryReadScope) -> MemorySnapshot:
        async with self._sessions()() as session:
            return await self._read_snapshot(session, scope)

    @staticmethod
    async def _read_snapshot(session: AsyncSession, scope: MemoryReadScope) -> MemorySnapshot:
        records = (await session.execute(_snapshot_statement(scope))).tuples().all()
        revision, epoch, as_of, _ = records[0]
        rows = tuple(_candidate(row) for _, _, _, row in records if row is not None)
        _validate_bounds(rows)
        return MemorySnapshot(scope, revision, epoch, as_of, rows)
