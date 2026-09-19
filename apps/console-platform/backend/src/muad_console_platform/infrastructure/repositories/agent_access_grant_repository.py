import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import AgentAccessGrant, AgentDefinition, PlatformUser


class AgentAccessGrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_active_grant(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> bool:
        grant_id: uuid.UUID | None = await self._session.scalar(
            select(AgentAccessGrant.id)
            .join(AgentDefinition, AgentDefinition.id == AgentAccessGrant.agent_id)
            .where(
                AgentAccessGrant.user_id == user_id,
                AgentAccessGrant.agent_id == agent_id,
                AgentAccessGrant.is_deleted.is_(False),
                AgentDefinition.tenant_id == tenant_id,
                AgentDefinition.is_deleted.is_(False),
            )
            .limit(1)
        )
        return grant_id is not None

    async def find_active(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> AgentAccessGrant | None:
        grant: AgentAccessGrant | None = await self._session.scalar(
            select(AgentAccessGrant).where(
                AgentAccessGrant.user_id == user_id,
                AgentAccessGrant.agent_id == agent_id,
                AgentAccessGrant.is_deleted.is_(False),
            )
        )
        return grant

    async def find_any(
        self,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> AgentAccessGrant | None:
        grant: AgentAccessGrant | None = await self._session.scalar(
            select(AgentAccessGrant).where(
                AgentAccessGrant.user_id == user_id,
                AgentAccessGrant.agent_id == agent_id,
            )
        )
        return grant

    async def list_by_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = (
            AgentAccessGrant.user_id == user_id,
            AgentAccessGrant.is_deleted.is_(False),
            AgentDefinition.is_deleted.is_(False),
            AgentDefinition.tenant_id == tenant_id,
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(AgentAccessGrant)
            .join(AgentDefinition, AgentDefinition.id == AgentAccessGrant.agent_id)
            .where(*conditions)
        )
        rows = (
            await self._session.execute(
                select(
                    AgentAccessGrant.agent_id,
                    AgentDefinition.key.label("agent_key"),
                    AgentDefinition.name.label("agent_name"),
                    AgentDefinition.enabled,
                    AgentAccessGrant.granted_at,
                    AgentAccessGrant.granted_by,
                )
                .join(AgentDefinition, AgentDefinition.id == AgentAccessGrant.agent_id)
                .where(*conditions)
                .order_by(AgentAccessGrant.granted_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).mappings()
        return [dict(row) for row in rows], int(total or 0)

    async def list_by_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = [
            AgentAccessGrant.agent_id == agent_id,
            AgentAccessGrant.is_deleted.is_(False),
            PlatformUser.is_deleted.is_(False),
        ]
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                PlatformUser.user_code.ilike(like) | PlatformUser.display_name.ilike(like)
            )
        total = await self._session.scalar(
            select(func.count())
            .select_from(AgentAccessGrant)
            .join(PlatformUser, PlatformUser.id == AgentAccessGrant.user_id)
            .where(*conditions)
        )
        rows = await self._session.execute(
            select(AgentAccessGrant, PlatformUser)
            .join(PlatformUser, PlatformUser.id == AgentAccessGrant.user_id)
            .where(*conditions)
            .order_by(AgentAccessGrant.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            {
                "user_id": user.id,
                "user_code": user.user_code,
                "display_name": user.display_name,
                "status": user.status,
                "granted_by": grant.granted_by,
                "granted_at": grant.granted_at,
                "create_time": grant.create_time,
            }
            for grant, user in rows.all()
        ]
        return items, int(total or 0)
