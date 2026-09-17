import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import AgentAccessGrant, AgentDefinition


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
