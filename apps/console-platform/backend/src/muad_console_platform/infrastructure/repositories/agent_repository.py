import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import AgentDefinition, ModelDefinition


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
    ) -> tuple[list[AgentDefinition], int]:
        conditions = (
            AgentDefinition.tenant_id == tenant_id,
            AgentDefinition.is_deleted.is_(False),
        )
        total = await self._session.scalar(
            select(func.count()).select_from(AgentDefinition).where(*conditions)
        )
        result = await self._session.scalars(
            select(AgentDefinition)
            .where(*conditions)
            .order_by(AgentDefinition.update_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.all()), int(total or 0)

    async def get(self, tenant_id: str, agent_id: uuid.UUID) -> AgentDefinition | None:
        agent: AgentDefinition | None = await self._session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.id == agent_id,
                AgentDefinition.tenant_id == tenant_id,
                AgentDefinition.is_deleted.is_(False),
            )
        )
        return agent

    async def find_by_key(self, tenant_id: str, key: str) -> AgentDefinition | None:
        agent: AgentDefinition | None = await self._session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.tenant_id == tenant_id,
                AgentDefinition.key == key,
                AgentDefinition.is_deleted.is_(False),
            )
        )
        return agent

    async def add(self, agent: AgentDefinition) -> AgentDefinition:
        self._session.add(agent)
        await self._session.flush()
        return agent

    async def get_model(self, tenant_id: str, model_id: uuid.UUID) -> ModelDefinition | None:
        model: ModelDefinition | None = await self._session.scalar(
            select(ModelDefinition).where(
                ModelDefinition.id == model_id,
                ModelDefinition.tenant_id == tenant_id,
                ModelDefinition.is_deleted.is_(False),
            )
        )
        return model
