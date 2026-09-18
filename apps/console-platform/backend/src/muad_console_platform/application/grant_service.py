import uuid
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import AgentAccessGrant
from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository
from ..infrastructure.repositories.agent_repository import AgentRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "AGENT_ACCESS_GRANT"


class GrantService:
    """用户↔Agent 单关系授权服务；07 的 Agent 侧视图复用同一实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._grants = AgentAccessGrantRepository(session)
        self._agents = AgentRepository(session)
        self._users = PlatformUserRepository(session)
        self._audit = AuditService(session)

    async def grant(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        granted_by: uuid.UUID,
        actor: AuditActor,
    ) -> AgentAccessGrant:
        await self._require_user(tenant_id, user_id)
        agent = await self._agents.get(tenant_id, agent_id)
        if agent is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        active = await self._grants.find_active(tenant_id, user_id, agent_id)
        if active is not None:
            return active
        now = datetime.now(UTC)
        grant = await self._grants.find_any(user_id, agent_id)
        action = "UPDATE"
        if grant is None:
            grant = AgentAccessGrant(
                user_id=user_id,
                agent_id=agent_id,
                granted_by=granted_by,
                granted_at=now,
            )
            self._session.add(grant)
            action = "CREATE"
        else:
            grant.is_deleted = False
            grant.granted_by = granted_by
            grant.granted_at = now
            grant.update_time = now
        await self._session.flush()
        await self._record_audit(tenant_id, actor, action, grant, granted_by)
        return grant

    async def revoke(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        await self._require_user(tenant_id, user_id)
        grant = await self._grants.find_active(tenant_id, user_id, agent_id)
        if grant is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        grant.is_deleted = True
        grant.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "DELETE", grant, grant.granted_by)

    async def list_by_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_user(tenant_id, user_id)
        return await self._grants.list_by_user(tenant_id, user_id, page, page_size)

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        grant: AgentAccessGrant,
        granted_by: uuid.UUID,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=grant.agent_id,
            action=action,
            before=None,
            after={
                "user_id": str(grant.user_id),
                "agent_id": str(grant.agent_id),
                "granted_by": str(granted_by),
            },
        )
