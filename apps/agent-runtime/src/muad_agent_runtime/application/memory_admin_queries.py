"""Admin projections over the v2 memory rows and their append-only history."""

from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import MemoryAuthor, ScopeState
from muad_contracts.memory_admin import (
    MemoryAdminItem,
    MemoryConflictItem,
    MemoryHistoryItem,
    MemoryStatus,
)
from sqlalchemy import and_, case, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import Case

from ..infrastructure.models.runtime import (
    MemoryConflictCandidate,
    UserMemory,
    UserMemoryHistory,
)


def _status() -> Case[str]:
    return case(
        (UserMemory.is_deleted.is_(True), "DELETED"),
        (UserMemory.expires_at <= func.statement_timestamp(), "EXPIRED"),
        (UserMemory.enabled.is_(False), "DISABLED"),
        else_="ACTIVE",
    )


class MemoryAdminQueries:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_entries(
        self,
        tenant: str,
        user: UUID,
        *,
        page: int,
        page_size: int,
        memory_kind: str | None = None,
        predicate: str | None = None,
        status: MemoryStatus | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[MemoryAdminItem], int]:
        where = and_(UserMemory.tenant_id == tenant, UserMemory.user_id == user)
        if not include_deleted:
            where &= UserMemory.is_deleted.is_(False)
        if memory_kind is not None:
            where &= UserMemory.memory_kind == memory_kind
        if predicate is not None:
            where &= UserMemory.predicate == predicate
        if status is not None:
            where &= _status() == status
        count = select(func.count().label("total")).select_from(UserMemory).where(where).subquery()
        page_rows = (
            select(UserMemory).where(where).order_by(UserMemory.update_time.desc(), UserMemory.id.desc())
        )
        page_rows = page_rows.offset((page - 1) * page_size).limit(page_size)
        projection = page_rows.subquery()
        record = aliased(UserMemory, projection)
        rows = (
            await self._session.execute(
                select(
                    count.c.total,
                    record,
                    case(
                        (record.is_deleted.is_(True), "DELETED"),
                        (record.expires_at <= func.statement_timestamp(), "EXPIRED"),
                        (record.enabled.is_(False), "DISABLED"),
                        else_="ACTIVE",
                    ),
                )
                .select_from(count.outerjoin(projection, true()))
                .order_by(record.update_time.desc(), record.id.desc())
            )
        ).all()
        items = [_item(row, state) for _, row, state in rows if row is not None]
        return items, int(rows[0][0])

    async def list_conflicts(
        self, tenant: str, user: UUID, *, page: int, page_size: int
    ) -> tuple[list[MemoryConflictItem], int]:
        where = and_(
            MemoryConflictCandidate.tenant_id == tenant,
            MemoryConflictCandidate.user_id == user,
            MemoryConflictCandidate.status == "PENDING",
            MemoryConflictCandidate.is_deleted.is_(False),
        )
        count = (
            select(func.count().label("total"))
            .select_from(MemoryConflictCandidate)
            .where(where)
            .subquery()
        )
        projection = (
            select(MemoryConflictCandidate)
            .where(where)
            .order_by(MemoryConflictCandidate.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .subquery()
        )
        record = aliased(MemoryConflictCandidate, projection)
        rows = (
            await self._session.execute(
                select(count.c.total, record)
                .select_from(count.outerjoin(projection, true()))
                .order_by(record.create_time.desc())
            )
        ).all()
        items = [_conflict(row) for _, row in rows if row is not None]
        return items, int(rows[0][0])

    async def history(
        self, tenant: str, user: UUID, memory_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[MemoryHistoryItem], int]:
        origin = await self._session.scalar(
            select(UserMemory.id).where(
                UserMemory.tenant_id == tenant,
                UserMemory.user_id == user,
                UserMemory.id == memory_id,
            )
        )
        if origin is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND)
        where = and_(
            UserMemoryHistory.tenant_id == tenant,
            UserMemoryHistory.user_id == user,
            UserMemoryHistory.origin_id == origin,
        )
        count = select(func.count().label("total")).select_from(UserMemoryHistory).where(where).subquery()
        page_rows = select(UserMemoryHistory).where(where).order_by(UserMemoryHistory.version.desc())
        projection = page_rows.offset((page - 1) * page_size).limit(page_size).subquery()
        record = aliased(UserMemoryHistory, projection)
        rows = (
            await self._session.execute(
                select(count.c.total, record)
                .select_from(count.outerjoin(projection, true()))
                .order_by(record.version.desc())
            )
        ).all()
        items = [_history(row) for _, row in rows if row is not None]
        return items, int(rows[0][0])


def _item(row: UserMemory, status: str) -> MemoryAdminItem:
    return MemoryAdminItem.model_validate(
        {
            "id": row.id,
            "memory_key": row.memory_key,
            "memory_kind": row.memory_kind,
            "predicate": row.predicate,
            "scope_state": row.scope_state,
            "scope": row.scope_json,
            "content": row.content_json,
            "title": row.title,
            "summary": row.summary,
            "tags": list(row.tags_json or ()),
            "cardinality": row.cardinality,
            "priority": row.priority,
            "author": MemoryAuthor(row.author),
            "authority": row.authority,
            "version": row.version,
            "enabled": row.enabled,
            "expires_at": row.expires_at,
            "last_confirmed_at": row.last_confirmed_at,
            "status": status,
            "create_time": row.create_time,
            "update_time": row.update_time,
        }
    )


def _history(row: UserMemoryHistory) -> MemoryHistoryItem:
    return MemoryHistoryItem(
        id=row.id,
        origin_id=row.origin_id,
        memory_key=row.memory_key,
        version=row.version,
        change_reason=row.change_reason,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        command_id=row.command_id,
        source_run_id=row.source_run_id,
        after=row.after_json,
        create_time=row.create_time,
    )


def _conflict(row: MemoryConflictCandidate) -> MemoryConflictItem:
    return MemoryConflictItem(
        id=row.id,
        memory_key=row.memory_key,
        predicate=row.predicate,
        scope_state=ScopeState(row.scope_state),
        title=row.title,
        summary=row.summary,
        content=row.content_json,
        base_version=row.base_version,
        forget_epoch=row.forget_epoch,
        reason=row.reason,
        create_time=row.create_time,
    )
