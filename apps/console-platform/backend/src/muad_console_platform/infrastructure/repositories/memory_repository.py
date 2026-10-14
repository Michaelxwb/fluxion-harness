"""Console 侧记忆只读投影：列表/历史 + 分页，作用域恒为 (tenant_id, user_id)。

写路径不在这里：Runtime 是唯一写入所有者，Console 的启禁用/删除/清空按 design §3.2.1
经受保护 API 转发（本模块的 `soft_delete*` 是尚未迁走的存量入口，只服务既有 DELETE 路由）。
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 状态口径与 Runtime `memory_admin_queries` 对齐：到期优先于禁用；已删除行不进任何状态。
STATUS_PROJECTION = (
    "CASE WHEN expires_at <= statement_timestamp() THEN 'EXPIRED' "
    "WHEN enabled = false THEN 'DISABLED' ELSE 'ACTIVE' END"
)
MEMORY_COLUMNS = (
    "id, memory_key, category, content_json AS content, author, explicit_request, "
    "confirmed_count, source_ref, source_quote, expires_at, last_confirmed_at, "
    f"version, enabled, create_time, update_time, {STATUS_PROJECTION} AS status"
)
HISTORY_COLUMNS = (
    "id, origin_id, memory_key, version, change_reason, actor_type, actor_id, command_id, "
    "source_run_id, after_json AS after, create_time"
)


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _where(
        tenant_id: str,
        user_id: uuid.UUID,
        *,
        category: str | None,
        author: str | None,
        status: str | None,
    ) -> tuple[str, dict[str, Any]]:
        """列表与计数共用同一谓词：筛选后 total 必须与 items 齐平。"""
        clauses = ["tenant_id = :tenant_id", "user_id = :user_id", "is_deleted = false"]
        params: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id}
        if category is not None:
            clauses.append("category = :category")
            params["category"] = category
        if author is not None:
            clauses.append("author = :author")
            params["author"] = author
        if status is not None:
            clauses.append(f"{STATUS_PROJECTION} = :status")
            params["status"] = status
        return " AND ".join(clauses), params

    async def list_by_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
        *,
        category: str | None = None,
        author: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = self._where(tenant_id, user_id, category=category, author=author, status=status)
        total = await self._session.scalar(
            text(f"SELECT count(*) FROM runtime.user_memory WHERE {where}"), params
        )
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {MEMORY_COLUMNS} FROM runtime.user_memory WHERE {where} "
                    "ORDER BY update_time DESC, id DESC OFFSET :offset LIMIT :limit"
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def find_origin(
        self, tenant_id: str, user_id: uuid.UUID, memory_key: str
    ) -> uuid.UUID | None:
        """按 key 取稳定 identity；**不过滤 is_deleted** —— 删除后历史仍可查。"""
        origin: uuid.UUID | None = await self._session.scalar(
            text(
                "SELECT id FROM runtime.user_memory "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id AND memory_key = :memory_key"
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "memory_key": memory_key},
        )
        return origin

    async def history(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        origin_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        where = "tenant_id = :tenant_id AND user_id = :user_id AND origin_id = :origin_id"
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "origin_id": origin_id,
        }
        total = await self._session.scalar(
            text(f"SELECT count(*) FROM runtime.user_memory_history WHERE {where}"), params
        )
        rows = (
            await self._session.execute(
                text(
                    f"SELECT {HISTORY_COLUMNS} FROM runtime.user_memory_history WHERE {where} "
                    "ORDER BY version DESC, id DESC OFFSET :offset LIMIT :limit"
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
