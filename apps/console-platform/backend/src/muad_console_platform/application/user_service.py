import uuid
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import PlatformUser
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from ..infrastructure.repositories.user_stats_repository import UserStatsRepository
from .audit_service import AuditActor, AuditService, sanitize_payload
from .dto import UserCreateRequest, UserUpdateRequest
from .grant_service import GrantService
from .memory_service import MemoryService

AUDIT_RESOURCE_TYPE = "PLATFORM_USER"


def user_snapshot(user: PlatformUser) -> dict[str, Any]:
    return {
        "user_code": user.user_code,
        "display_name": user.display_name,
        "status": user.status,
        "metadata": sanitize_payload(user.metadata_json),
    }


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = PlatformUserRepository(session)
        self._stats = UserStatsRepository(session)
        self._audit = AuditService(session)

    async def list_users(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None,
        status: str | None,
    ) -> tuple[list[PlatformUser], dict[uuid.UUID, dict[str, int]], int]:
        users, total = await self._users.list(tenant_id, page, page_size, keyword, status)
        stats = await self._stats.counts(tenant_id, [user.id for user in users])
        return users, stats, total

    async def get_user(self, tenant_id: str, user_id: uuid.UUID) -> tuple[PlatformUser, dict[str, int]]:
        user = await self._require_user(tenant_id, user_id)
        stats = await self._stats.counts(tenant_id, [user.id])
        return user, stats[user.id]

    async def list_agent_grants(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        return await GrantService(self._session).list_by_user(tenant_id, user_id, page, page_size)

    async def list_memories(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
        category: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        return await MemoryService(self._session).list_by_user(tenant_id, user_id, page, page_size, category)

    async def create_user(
        self,
        tenant_id: str,
        payload: UserCreateRequest,
        actor: AuditActor,
    ) -> PlatformUser:
        if await self._users.find_by_code(tenant_id, payload.user_code) is not None:
            raise self._conflict(payload.user_code)
        user = PlatformUser(
            tenant_id=tenant_id,
            user_code=payload.user_code,
            display_name=payload.display_name,
            status=payload.status,
            metadata_json=payload.metadata,
        )
        try:
            created = await self._users.add(user)
        except IntegrityError as exc:
            raise self._conflict(payload.user_code) from exc
        await self._record_audit(tenant_id, actor, "CREATE", None, created)
        return created

    async def update_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        payload: UserUpdateRequest,
        actor: AuditActor,
    ) -> PlatformUser:
        user = await self._users.get_for_update(tenant_id, user_id)
        if user is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        before = user_snapshot(user)
        if payload.display_name is not None:
            user.display_name = payload.display_name
        if payload.status is not None:
            user.status = payload.status
        if payload.metadata is not None:
            user.metadata_json = payload.metadata
        user.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "UPDATE", before, user)
        return user

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> PlatformUser:
        user = await self._users.get(tenant_id, user_id)
        if user is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return user

    def _conflict(self, user_code: str) -> AppError:
        return AppError(ErrorCode.USER_CODE_EXISTS, message_args={"user_code": user_code})

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        before: dict[str, Any] | None,
        after: PlatformUser,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=after.id,
            action=action,
            before=before,
            after=user_snapshot(after),
        )
