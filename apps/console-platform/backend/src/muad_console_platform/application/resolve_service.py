from typing import Literal, cast

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedAgent,
    ResolvedModel,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository
from ..infrastructure.repositories.agent_repository import AgentRepository


class ResolveService:
    def __init__(self, session: AsyncSession) -> None:
        self._agents = AgentRepository(session)
        self._grants = AgentAccessGrantRepository(session)

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
                secret_ref=model.secret_ref,
                params=model.params_json,
            ),
        )
