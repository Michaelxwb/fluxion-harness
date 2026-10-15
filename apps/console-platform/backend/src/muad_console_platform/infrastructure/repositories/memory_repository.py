"""Console 侧记忆只读投影：列表/历史 + 分页，作用域恒为 (tenant_id, user_id)。

写路径不在这里，也不在 Console：Runtime 是唯一写入所有者，启禁用/删除/清空按 design §3.2.1
经受保护 API 转发（`application/memory_service.py` 的 `MemoryAdminService`）。本模块只发 SELECT。
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

    async def _paged_snapshot(
        self,
        table: str,
        columns: str,
        where: str,
        params: dict[str, Any],
        order_by: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """count 与分页行在**同一条语句**里产出。

        READ COMMITTED 下两条语句各取快照：并发写入或 `expires_at` 跨界会让 count 与行集
        分属不同集合（评审实测 total=0 而 items 有 1 条）。count 子查询与行子查询共用谓词，
        同一语句内 `statement_timestamp()` 恒定；count 恒有 1 行，LEFT JOIN 保证空页也带出 total。
        """
        rows = (
            await self._session.execute(
                text(
                    f"SELECT snapshot.total, page_rows.* FROM "
                    f"(SELECT count(*) AS total FROM {table} WHERE {where}) AS snapshot "
                    f"LEFT JOIN (SELECT {columns} FROM {table} WHERE {where} "
                    f"ORDER BY {order_by} OFFSET :offset LIMIT :limit) AS page_rows ON true "
                    f"ORDER BY {order_by}"
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings().all()
        total = int(rows[0]["total"])
        items = [
            {key: value for key, value in row.items() if key != "total"}
            for row in rows
            if row["id"] is not None
        ]
        return items, total

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
        return await self._paged_snapshot(
            "runtime.user_memory",
            MEMORY_COLUMNS,
            where,
            params,
            "update_time DESC, id DESC",
            page,
            page_size,
        )

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
        return await self._paged_snapshot(
            "runtime.user_memory_history",
            HISTORY_COLUMNS,
            where,
            params,
            "version DESC, id DESC",
            page,
            page_size,
        )
