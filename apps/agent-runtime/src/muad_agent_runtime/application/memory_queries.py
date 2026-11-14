"""Complete candidate reads: rows, revision and current time share one SQL snapshot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from muad_agent_core.memory.rank import ExperienceRecord, record_tokens
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    MEMORY_MAX_BYTES,
    MEMORY_MAX_RECORDS,
    ExperienceCandidate,
    MemoryAuthor,
    memory_content_bytes,
)
from pydantic import JsonValue
from sqlalchemy import Select, func, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import FromClause

from ..infrastructure.db import SessionFactory, get_session_factory
from ..infrastructure.models.runtime import LearningFamilyControl, MemoryScope, UserMemory


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
class ExperienceRow:
    id: UUID
    record: ExperienceRecord
    source_quote: str | None


@dataclass(frozen=True, slots=True)
class ExperienceSnapshot:
    scope: MemoryReadScope
    revision: int
    forget_epoch: int
    as_of: datetime
    rows: tuple[ExperienceRow, ...]

    @property
    def records(self) -> tuple[ExperienceRecord, ...]:
        return tuple(row.record for row in self.rows)


@dataclass(frozen=True, slots=True)
class MemoryCandidateRow:
    id: UUID
    memory_key: str
    memory_kind: str
    predicate: str
    scope_state: str
    scope: dict[str, str]
    content: dict[str, JsonValue]
    title: str
    summary: str
    tags: tuple[str, ...]
    cardinality: str
    priority: str
    author: str
    authority: str
    version: int
    enabled: bool
    expires_at: datetime | None
    last_confirmed_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """One SQL snapshot: v2 rows and legacy-lane rows share revision/epoch/as_of."""

    scope: MemoryReadScope
    revision: int
    forget_epoch: int
    as_of: datetime
    rows: tuple[MemoryCandidateRow, ...]
    legacy_rows: tuple[ExperienceRow, ...] = ()


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
        & ~sa.exists().where(
            LearningFamilyControl.tenant_id == scope.tenant_id,
            LearningFamilyControl.family == UserMemory.task_signature,
            LearningFamilyControl.paused.is_(True),
            LearningFamilyControl.is_deleted.is_(False),
        )
    )
    return (
        select(*head.selected_columns, UserMemory)
        .select_from(
            source.outerjoin(UserMemory.__table__, live),
        )
        .order_by(UserMemory.memory_key)
    )


def _candidate(row: UserMemory) -> ExperienceRow:
    candidate = ExperienceCandidate.model_validate(
        {
            "memory_key": row.memory_key,
            "category": row.category,
            "content": row.content_json,
        }
    )
    record = ExperienceRecord(
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
        activation_state=row.activation_state,
        task_signature=row.task_signature,
        trigger_text=row.trigger_text,
    )
    # token 集在缓存装载时预计算，请求期只做集合运算（NFR-PERF-01）。
    record = replace(record, tokens=record_tokens(record))
    return ExperienceRow(row.id, record, row.source_quote)


def _is_v2_row(row: UserMemory | None) -> bool:
    """v2 lane is identified by memory_kind; the legacy lane never fills it."""

    return row is not None and row.memory_kind is not None


def _is_legacy_row(row: UserMemory | None) -> bool:
    """Legacy lane keeps category (experience / TASK_STATE) and carries no memory_kind."""

    return row is not None and row.memory_kind is None and row.category is not None


def _str_map(value: object) -> dict[str, str]:
    """scope_json 是 JSONB：只接受字符串键值，坏形状按空 scope 处理（键值本身来自写入校验）。"""

    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if isinstance(item, str)}


def _candidate_v2(row: UserMemory) -> MemoryCandidateRow:
    assert row.memory_kind is not None  # v2 lane 由 memory_kind 判定
    return MemoryCandidateRow(
        id=row.id,
        memory_key=row.memory_key,
        memory_kind=row.memory_kind,
        predicate=row.predicate or "general",
        scope_state=row.scope_state or "ABSENT",
        scope=_str_map(row.scope_json),
        content=dict(row.content_json or {}),
        title=row.title or "",
        summary=row.summary or "",
        tags=tuple(row.tags_json or ()),
        cardinality=row.cardinality or "SINGLE",
        priority=row.priority or "OPTIONAL",
        author=row.author,
        authority=row.authority,
        version=row.version,
        enabled=row.enabled,
        expires_at=row.expires_at,
        last_confirmed_at=row.last_confirmed_at,
        updated_at=row.update_time,
    )


def _validate_snapshot_bounds(rows: tuple[UserMemory, ...]) -> None:
    """The complete bounded set fails closed instead of returning a silently truncated view."""

    if len(rows) > MEMORY_MAX_RECORDS or sum(
        memory_content_bytes(row.content_json, row.source_quote) for row in rows
    ) > MEMORY_MAX_BYTES:
        raise AppError(ErrorCode.MEMORY_READ_FAILED)


def _validate_bounds(rows: tuple[ExperienceRow, ...]) -> None:
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
            return await self.read_scope_version_in_session(session, scope)

    @staticmethod
    async def read_scope_version_in_session(
        session: AsyncSession, scope: MemoryReadScope
    ) -> MemoryScopeVersion:
        revision, epoch, as_of = (await session.execute(_head_statement(scope))).tuples().one()
        return MemoryScopeVersion(revision, epoch, as_of)

    async def read_experience_snapshot(self, scope: MemoryReadScope) -> ExperienceSnapshot:
        async with self._sessions()() as session:
            return await self._read_experience_snapshot(session, scope)

    async def read_snapshot(self, scope: MemoryReadScope) -> MemorySnapshot:
        """Read both lanes from one SQL snapshot; neither lane is derived from the other."""

        async with self._sessions()() as session:
            records = (await session.execute(_snapshot_statement(scope))).tuples().all()
            _validate_snapshot_bounds(tuple(row for _, _, _, row in records if row is not None))
            revision, epoch, as_of, _ = records[0]
            rows = tuple(_candidate_v2(row) for _, _, _, row in records if _is_v2_row(row))
            legacy = tuple(_candidate(row) for _, _, _, row in records if _is_legacy_row(row))
            return MemorySnapshot(scope, revision, epoch, as_of, rows, legacy)

    @staticmethod
    async def _read_experience_snapshot(session: AsyncSession, scope: MemoryReadScope) -> ExperienceSnapshot:
        records = (await session.execute(_snapshot_statement(scope))).tuples().all()
        revision, epoch, as_of, _ = records[0]
        rows = tuple(_candidate(row) for _, _, _, row in records if _is_legacy_row(row))
        _validate_bounds(rows)
        return ExperienceSnapshot(scope, revision, epoch, as_of, rows)
