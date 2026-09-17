import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import AgentSkillBinding, Skill, SkillArtifact
from .skill_repository import current_artifact_join


class AgentSkillBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, agent_id: uuid.UUID, skill_id: uuid.UUID) -> AgentSkillBinding | None:
        binding: AgentSkillBinding | None = await self._session.scalar(
            select(AgentSkillBinding).where(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.skill_id == skill_id,
            )
        )
        return binding

    async def list_for_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
    ) -> list[tuple[AgentSkillBinding, Skill, SkillArtifact | None]]:
        result = await self._session.execute(
            select(AgentSkillBinding, Skill, SkillArtifact)
            .join(Skill, Skill.id == AgentSkillBinding.skill_id)
            .outerjoin(SkillArtifact, current_artifact_join())
            .where(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.is_deleted.is_(False),
                Skill.tenant_id == tenant_id,
                Skill.is_deleted.is_(False),
            )
            .order_by(AgentSkillBinding.sort_order, Skill.key)
        )
        return [(binding, skill, artifact) for binding, skill, artifact in result.all()]

    async def add(self, binding: AgentSkillBinding) -> AgentSkillBinding:
        self._session.add(binding)
        await self._session.flush()
        return binding
