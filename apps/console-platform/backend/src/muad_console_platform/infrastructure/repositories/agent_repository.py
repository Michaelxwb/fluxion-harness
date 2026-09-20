import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import BotAccount
from ..models.control import AgentAccessGrant, AgentDefinition, AgentSkillBinding, ModelDefinition
from ..models.mcp import AgentMcpBinding


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None = None,
        enabled: bool | None = None,
    ) -> tuple[list[AgentDefinition], int]:
        conditions = [
            AgentDefinition.tenant_id == tenant_id,
            AgentDefinition.is_deleted.is_(False),
        ]
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                AgentDefinition.name.ilike(like)
                | AgentDefinition.key.ilike(like)
            )
        if enabled is not None:
            conditions.append(AgentDefinition.enabled.is_(enabled))
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

    async def aggregate_counts(
        self, tenant_id: str
    ) -> tuple[
        dict[uuid.UUID, int],
        dict[uuid.UUID, int],
        dict[uuid.UUID, int],
        dict[uuid.UUID, int],
    ]:
        """批量聚合 skill/mcp/channel/user 有效关系计数（无 N+1）。"""

        skill_rows = await self._session.execute(
            select(AgentSkillBinding.agent_id, func.count())
            .join(AgentDefinition, AgentDefinition.id == AgentSkillBinding.agent_id)
            .where(
                AgentDefinition.tenant_id == tenant_id,
                AgentDefinition.is_deleted.is_(False),
                AgentSkillBinding.is_deleted.is_(False),
            )
            .group_by(AgentSkillBinding.agent_id)
        )
        mcp_rows = await self._session.execute(
            select(AgentMcpBinding.agent_id, func.count())
            .join(AgentDefinition, AgentDefinition.id == AgentMcpBinding.agent_id)
            .where(
                AgentDefinition.tenant_id == tenant_id,
                AgentDefinition.is_deleted.is_(False),
                AgentMcpBinding.is_deleted.is_(False),
            )
            .group_by(AgentMcpBinding.agent_id)
        )
        channel_rows = await self._session.execute(
            select(BotAccount.agent_id, func.count())
            .where(
                BotAccount.tenant_id == tenant_id,
                BotAccount.is_deleted.is_(False),
            )
            .group_by(BotAccount.agent_id)
        )
        grant_rows = await self._session.execute(
            select(AgentAccessGrant.agent_id, func.count())
            .where(
                AgentAccessGrant.is_deleted.is_(False),
                AgentAccessGrant.agent_id.in_(
                    select(AgentDefinition.id).where(
                        AgentDefinition.tenant_id == tenant_id,
                        AgentDefinition.is_deleted.is_(False),
                    )
                ),
            )
            .group_by(AgentAccessGrant.agent_id)
        )
        return (
            {row[0]: int(row[1]) for row in skill_rows.all()},
            {row[0]: int(row[1]) for row in mcp_rows.all()},
            {row[0]: int(row[1]) for row in channel_rows.all()},
            {row[0]: int(row[1]) for row in grant_rows.all()},
        )

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
