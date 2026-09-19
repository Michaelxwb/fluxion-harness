from typing import Literal, cast

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository
from ..infrastructure.repositories.agent_repository import AgentRepository
from ..infrastructure.repositories.skill_repository import SkillRepository
from .agent_mcp_service import AgentMcpService


class ResolveService:
    def __init__(self, session: AsyncSession) -> None:
        self._agents = AgentRepository(session)
        self._grants = AgentAccessGrantRepository(session)
        self._skills = SkillRepository(session)
        self._mcp = AgentMcpService(session)

    async def resolve_definition(
        self,
        tenant_id: str,
        payload: ResolveDefinitionRequest,
    ) -> ResolveDefinitionResponse:
        agent = await self._agents.get(tenant_id, payload.agent_id)
        if agent is None:
            raise AppError(ErrorCode.AGENT_NOT_FOUND)
        if not agent.enabled:
            raise AppError(ErrorCode.AGENT_DISABLED)
        granted = await self._grants.has_active_grant(
            tenant_id,
            payload.actor_user_id,
            payload.agent_id,
        )
        if not granted:
            raise AppError(ErrorCode.AGENT_ACCESS_DENIED)
        model = await self._agents.get_model(tenant_id, agent.model_id)
        if model is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "Model"})
        if not model.enabled:
            raise AppError(ErrorCode.MODEL_DISABLED)
        return ResolveDefinitionResponse(
            agent=ResolvedAgent(
                id=agent.id,
                key=agent.key,
                revision=agent.revision,
                instructions=agent.instructions,
                runtime_config=agent.runtime_config,
            ),
            model=ResolvedModel(
                id=model.id,
                revision=model.revision,
                protocol=cast(Literal["OPENAI"], model.protocol),
                model_id=model.model_id,
                base_url=model.base_url,
                api_key=model.api_key,
                params=model.params_json,
            ),
            skills=await self._resolved_skills(tenant_id, payload),
            mcp_servers=await self._mcp.effective_mcp_servers(
                tenant_id, payload.agent_id, payload.actor_user_id
            ),
        )

    async def _resolved_skills(
        self,
        tenant_id: str,
        payload: ResolveDefinitionRequest,
    ) -> list[ResolvedSkill]:
        rows = await self._skills.list_effective_for_agent(
            tenant_id,
            payload.agent_id,
            payload.actor_user_id,
        )
        return [
            ResolvedSkill(
                skill_id=skill.id,
                artifact_id=artifact.id,
                key=skill.key,
                name=skill.name,
                description=skill.description,
                version=artifact.version,
                checksum=artifact.checksum,
                storage_key=artifact.storage_key,
                execution_mode=cast(SkillExecutionMode, artifact.execution_mode),
                frontmatter=artifact.frontmatter_json,
            )
            for skill, artifact in rows
        ]
