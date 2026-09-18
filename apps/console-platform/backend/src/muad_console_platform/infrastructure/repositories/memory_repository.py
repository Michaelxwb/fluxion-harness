import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MEMORY_COLUMNS = (
    "id, memory_key, category, content_json AS content, source_type, source_ref, "
    "version, enabled, create_time, update_time"
)


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
        category: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = "tenant_id = :tenant_id AND user_id = :user_id AND is_deleted = false"
        params: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id}
        if category:
            filters += " AND category = :category"
            params["category"] = category
        total = await self._session.scalar(
            text(f"SELECT count(*) FROM runtime.user_memory WHERE {filters}"), params
        )
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {MEMORY_COLUMNS} FROM runtime.user_memory WHERE {filters} "
                    "ORDER BY update_time DESC OFFSET :offset LIMIT :limit"
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def get_for_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT id, memory_key FROM runtime.user_memory "
                    "WHERE id = :id AND tenant_id = :tenant_id AND user_id = :user_id "
                    "AND is_deleted = false"
                ),
                {"id": memory_id, "tenant_id": tenant_id, "user_id": user_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def soft_delete(self, tenant_id: str, user_id: uuid.UUID, memory_id: uuid.UUID) -> None:
        await self._session.execute(
            text(
                "UPDATE runtime.user_memory SET is_deleted = true, update_time = now() "
                "WHERE id = :id AND tenant_id = :tenant_id AND user_id = :user_id "
                "AND is_deleted = false"
            ),
            {"id": memory_id, "tenant_id": tenant_id, "user_id": user_id},
        )
        await self._session.flush()

    async def soft_delete_all(self, tenant_id: str, user_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT id, memory_key FROM runtime.user_memory "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id AND is_deleted = false"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
        ).mappings()
        deleted = [dict(row) for row in rows]
        if deleted:
            await self._session.execute(
                text(
                    "UPDATE runtime.user_memory SET is_deleted = true, update_time = now() "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id AND is_deleted = false"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
            await self._session.flush()
        return deleted
