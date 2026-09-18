import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.memory_repository import MemoryRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "USER_MEMORY"


class MemoryService:
    """用户 Memory 读写服务；08 的 Runtime 侧复用同一实现。"""

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
        category: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_user(tenant_id, user_id)
        return await self._memories.list_by_user(tenant_id, user_id, page, page_size, category)

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
