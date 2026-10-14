"""One SQL snapshot supplies paged items and total; stable identities include history."""

from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import MemoryAuthor, MemoryCategory
from muad_contracts.memory_admin import MemoryAdminItem, MemoryHistoryItem, MemoryStatus
from sqlalchemy import and_, case, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import Case

from ..infrastructure.models.runtime import UserMemory, UserMemoryHistory


def _status() -> Case[str]:
    return case(
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
        category: MemoryCategory | None = None,
        author: MemoryAuthor | None = None,
        status: MemoryStatus | None = None,
    ) -> tuple[list[MemoryAdminItem], int]:
        where = and_(
            UserMemory.tenant_id == tenant, UserMemory.user_id == user, UserMemory.is_deleted.is_(False)
        )
        if category is not None:
            where &= UserMemory.category == category.value
        if author is not None:
            where &= UserMemory.author == author.value
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

    async def history(
        self, tenant: str, user: UUID, key: str, *, page: int, page_size: int
    ) -> tuple[list[MemoryHistoryItem], int]:
        origin = await self._session.scalar(
            select(UserMemory.id).where(
                UserMemory.tenant_id == tenant, UserMemory.user_id == user, UserMemory.memory_key == key
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
            "category": row.category,
            "content": row.content_json,
            "author": row.author,
            "version": row.version,
            "enabled": row.enabled,
            "explicit_request": row.explicit_request,
            "confirmed_count": row.confirmed_count,
            "status": status,
            "source_ref": row.source_ref,
            "source_quote": row.source_quote,
            "expires_at": row.expires_at,
            "last_confirmed_at": row.last_confirmed_at,
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
        legacy_snapshots=row.legacy_snapshots_json,
        create_time=row.create_time,
    )
