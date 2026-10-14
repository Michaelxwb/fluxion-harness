import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.memory_repository import MemoryRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from .audit_query_service import format_console_time
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "USER_MEMORY"

_MEMORY_TIME_FIELDS = ("expires_at", "last_confirmed_at", "create_time", "update_time")


def _memory_view(row: dict[str, Any]) -> dict[str, Any]:
    """Console 时间投影复用统一入口 `format_console_time`（design §3.7）。"""
    return {**row, **{field: format_console_time(row[field]) for field in _MEMORY_TIME_FIELDS}}


def _history_view(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "create_time": format_console_time(row["create_time"])}


class MemoryService:
    """Console 记忆管理适配：读列表/历史走只读仓储，写操作按 design §3.2.1 归 Runtime 唯一

    writer（Console 经受保护内部 API 转发）；本类保留的 Delete/Clear 是尚未迁走的存量实现。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._memories = MemoryRepository(session)
        self._users = PlatformUserRepository(session)
        self._audit = AuditService(session)

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
        await self._require_user(tenant_id, user_id)
        items, total = await self._memories.list_by_user(
            tenant_id, user_id, page, page_size, category=category, author=author, status=status
        )
        return [_memory_view(item) for item in items], total

    async def history(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        memory_key: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """按 key 读版本历史：deleted 的记忆同样可查，未知 key 明确 NOT_FOUND。"""
        await self._require_user(tenant_id, user_id)
        origin_id = await self._memories.find_origin(tenant_id, user_id, memory_key)
        if origin_id is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND)
        items, total = await self._memories.history(tenant_id, user_id, origin_id, page, page_size)
        return [_history_view(item) for item in items], total

    async def delete(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        await self._require_user(tenant_id, user_id)
        memory = await self._memories.get_for_user(tenant_id, user_id, memory_id)
        if memory is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        await self._memories.soft_delete(tenant_id, user_id, memory_id)
        await self._record_audit(tenant_id, actor, memory_id, memory["memory_key"])

    async def delete_all(self, tenant_id: str, user_id: uuid.UUID, actor: AuditActor) -> int:
        await self._require_user(tenant_id, user_id)
        deleted = await self._memories.soft_delete_all(tenant_id, user_id)
        for row in deleted:
            await self._record_audit(tenant_id, actor, row["id"], row["memory_key"])
        return len(deleted)

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        memory_id: uuid.UUID,
        memory_key: str,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=memory_id,
            action="DELETE",
            before=None,
            after={"memory_key": memory_key},
        )
