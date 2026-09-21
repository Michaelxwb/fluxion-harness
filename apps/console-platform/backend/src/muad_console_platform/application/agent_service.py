import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import AgentDefinition, ModelDefinition, SkillImportIdempotency
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
        rows = await self._session.execute(
            select(ModelDefinition.id, ModelDefinition.name).where(
                ModelDefinition.tenant_id == tenant_id,
                ModelDefinition.is_deleted.is_(False),
            )
        )
        return {row[0]: row[1] for row in rows.all()}

    async def get_agent_detail(self, tenant_id: str, agent_id: uuid.UUID) -> dict[str, Any]:
        agent = await self.get_agent(tenant_id, agent_id)
        detail = AgentListItem.model_validate(agent).model_dump(mode="json")
        detail["model_name"] = await self._session.scalar(
            select(ModelDefinition.name).where(
                ModelDefinition.id == agent.model_id,
                ModelDefinition.is_deleted.is_(False),
            )
        ) or ""
        detail["instructions"] = agent.instructions
        detail["runtime_config"] = agent.runtime_config
        detail["create_time"] = agent.create_time.isoformat()
        detail.update(await self.agent_counts(tenant_id, agent.id))
        return detail

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
        idempotency_key: str | None = None,
    ) -> AgentDefinition:
        if idempotency_key:
            await self._lock_idempotency(tenant_id, idempotency_key)
            replayed = await self._idempotency_replay(
                tenant_id, idempotency_key, payload
            )
            if replayed is not None:
                return await self.get_agent(tenant_id, uuid.UUID(replayed["id"]))
        await self._require_model(tenant_id, payload.model_id)
        existing = await self._agents.find_by_key(tenant_id, payload.key)
        if existing is not None:
            raise AppError(ErrorCode.AGENT_KEY_EXISTS, message_args={"key": payload.key})
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
            raise AppError(ErrorCode.AGENT_KEY_EXISTS, message_args={"key": payload.key}) from exc
        await self._record_audit(tenant_id, actor, "CREATE", created.id, None, created)
        if idempotency_key:
            self._session.add(
                SkillImportIdempotency(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    endpoint="agent-create",
                    request_fingerprint=self._fingerprint(payload),
                    response_json={"id": str(created.id)},
                )
            )
            await self._session.flush()
        return created

    def _fingerprint(self, payload: AgentCreateRequest) -> str:
        body = payload.model_dump_json(exclude={"key"})
        return "sha256:" + hashlib.sha256(
            (body + "|" + payload.key).encode()
        ).hexdigest()

    async def _lock_idempotency(self, tenant_id: str, idempotency_key: str) -> None:
        digest = hashlib.sha256(f"{tenant_id}|{idempotency_key}|agent-create".encode()).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _idempotency_replay(
        self, tenant_id: str, key: str, payload: AgentCreateRequest
    ) -> dict[str, Any] | None:
        row = await self._session.execute(
            select(SkillImportIdempotency).where(
                SkillImportIdempotency.tenant_id == tenant_id,
                SkillImportIdempotency.idempotency_key == key,
                SkillImportIdempotency.endpoint == "agent-create",
                SkillImportIdempotency.is_deleted.is_(False),
            )
        )
        record = row.scalar_one_or_none()
        if record is None:
            return None
        if record.request_fingerprint != self._fingerprint(payload):
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        return dict(record.response_json)

    async def update_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        payload: AgentUpdateRequest,
        actor: AuditActor,
    ) -> dict[str, Any]:
        agent = await self.get_agent(tenant_id, agent_id)
        if payload.model_id is not None and payload.model_id != agent.model_id:
            await self._require_model(tenant_id, payload.model_id)
        before = agent_snapshot(agent)
        updates = payload.model_dump(exclude_unset=True, exclude={"expected_revision"})
        updates["revision"] = agent.revision + 1
        updates["update_time"] = datetime.now(UTC)
        # 单语句 CAS：并发窗口在数据库行锁内消除
        result = await self._session.execute(
            update(AgentDefinition)
            .where(
                AgentDefinition.id == agent_id,
                AgentDefinition.tenant_id == tenant_id,
                AgentDefinition.is_deleted.is_(False),
                AgentDefinition.revision == payload.expected_revision,
            )
            .values(**updates)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise AppError(ErrorCode.REVISION_CONFLICT)
        await self._session.flush()
        updated = await self.get_agent(tenant_id, agent_id)
        await self._record_audit(tenant_id, actor, "UPDATE", agent_id, before, updated)
        return {
            "id": str(agent_id),
            "revision": updated.revision,
            "update_time": updated.update_time.isoformat(),
        }

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
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if not model.enabled:
            raise AppError(ErrorCode.MODEL_DISABLED)
