import uuid
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import AgentDefinition
from ..infrastructure.repositories.agent_repository import AgentRepository
from .audit_service import AuditActor, AuditService, sanitize_payload
from .dto import AgentCreateRequest, AgentListItem, AgentUpdateRequest

AUDIT_RESOURCE_TYPE = "AGENT"


def agent_snapshot(agent: AgentDefinition) -> dict[str, Any]:
    return {
        "key": agent.key,
        "name": agent.name,
        "description": agent.description,
        "instructions": agent.instructions,
        "model_id": str(agent.model_id),
        "runtime_config": sanitize_payload(agent.runtime_config),
        "revision": agent.revision,
        "enabled": agent.enabled,
    }


class AgentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._agents = AgentRepository(session)
        self._audit = AuditService(session)

    async def list_agents(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None = None,
        enabled: bool | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        agents, total = await self._agents.list(
            tenant_id, page, page_size, keyword=keyword, enabled=enabled
        )
        skill_counts, mcp_counts, channel_counts, user_counts = (
            await self._agents.aggregate_counts(tenant_id)
        )
        model_names = await self._model_names(tenant_id)
        items = []
        for agent in agents:
            item = AgentListItem.model_validate(agent).model_dump(mode="json")
            item["model_name"] = model_names.get(agent.model_id, "")
            item["skill_count"] = skill_counts.get(agent.id, 0)
            item["mcp_count"] = mcp_counts.get(agent.id, 0)
            item["channel_count"] = channel_counts.get(agent.id, 0)
            item["user_count"] = user_counts.get(agent.id, 0)
            items.append(item)
        return items, total

    async def _model_names(self, tenant_id: str) -> dict[uuid.UUID, str]:
        from sqlalchemy import select

        from ..infrastructure.models.control import ModelDefinition

        rows = await self._session.execute(
            select(ModelDefinition.id, ModelDefinition.name).where(
                ModelDefinition.tenant_id == tenant_id,
                ModelDefinition.is_deleted.is_(False),
            )
        )
        return {row[0]: row[1] for row in rows.all()}

    async def agent_counts(self, tenant_id: str, agent_id: uuid.UUID) -> dict[str, int]:
        skill_counts, mcp_counts, channel_counts, user_counts = (
            await self._agents.aggregate_counts(tenant_id)
        )
        return {
            "skill_count": skill_counts.get(agent_id, 0),
            "mcp_count": mcp_counts.get(agent_id, 0),
            "channel_count": channel_counts.get(agent_id, 0),
            "user_count": user_counts.get(agent_id, 0),
        }

    async def get_agent(self, tenant_id: str, agent_id: uuid.UUID) -> AgentDefinition:
        agent = await self._agents.get(tenant_id, agent_id)
        if agent is None:
            raise AppError(ErrorCode.AGENT_NOT_FOUND)
        return agent

    async def create_agent(
        self,
        tenant_id: str,
        payload: AgentCreateRequest,
        actor: AuditActor,
    ) -> AgentDefinition:
        await self._require_model(tenant_id, payload.model_id)
        existing = await self._agents.find_by_key(tenant_id, payload.key)
        if existing is not None:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=payload.key,
            name=payload.name,
            description=payload.description,
            instructions=payload.instructions,
            model_id=payload.model_id,
            runtime_config=payload.runtime_config,
            enabled=payload.enabled,
        )
        try:
            created = await self._agents.add(agent)
        except IntegrityError as exc:
            raise AppError(ErrorCode.COMMON_CONFLICT) from exc
        await self._record_audit(tenant_id, actor, "CREATE", created.id, None, created)
        return created

    async def update_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        payload: AgentUpdateRequest,
        actor: AuditActor,
    ) -> AgentDefinition:
        agent = await self.get_agent(tenant_id, agent_id)
        if payload.expected_revision != agent.revision:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        if payload.model_id is not None and payload.model_id != agent.model_id:
            await self._require_model(tenant_id, payload.model_id)
        before = agent_snapshot(agent)
        updates = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        for field, value in updates.items():
            setattr(agent, field, value)
        agent.revision += 1
        agent.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "UPDATE", agent.id, before, agent)
        return agent

    async def delete_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        actor: AuditActor,
    ) -> None:
        agent = await self.get_agent(tenant_id, agent_id)
        before = agent_snapshot(agent)
        agent.is_deleted = True
        agent.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._record_audit(tenant_id, actor, "DELETE", agent.id, before, None)

    async def _record_audit(
        self,
        tenant_id: str,
        actor: AuditActor,
        action: str,
        resource_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: AgentDefinition | None,
    ) -> None:
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=resource_id,
            action=action,
            before=before,
            after=agent_snapshot(after) if after is not None else None,
        )

    async def _require_model(self, tenant_id: str, model_id: uuid.UUID) -> None:
        model = await self._agents.get_model(tenant_id, model_id)
        if model is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "Model"})
        if not model.enabled:
            raise AppError(ErrorCode.MODEL_DISABLED)
