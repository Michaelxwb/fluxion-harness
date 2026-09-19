"""Agent-MCP 绑定（单关系 POST/DELETE 独立事务）与 EffectiveMcp resolve。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from muad_contracts import ResolvedMcpServer

from ..infrastructure.models.control import AgentDefinition
from ..infrastructure.models.mcp import AgentMcpBinding, McpServer, McpUserGrant
from ..infrastructure.repositories.agent_mcp_binding_repository import (
    AgentMcpBindingRepository,
)
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "AGENT"


def binding_snapshot(binding: AgentMcpBinding) -> dict[str, Any]:
    return {
        "agent_id": str(binding.agent_id),
        "mcp_server_id": str(binding.mcp_server_id),
    }


def binding_item(binding: AgentMcpBinding, server: McpServer) -> dict[str, Any]:
    return {
        "mcp_server_id": str(server.id),
        "key": server.key,
        "name": server.name,
        "user_scope": server.user_scope,
        "enabled": server.enabled,
        "connection_status": server.connection_status,
        "tool_count": len(server.tool_catalog_json or []),
        "create_time": binding.create_time.isoformat(),
    }


class AgentMcpService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._bindings = AgentMcpBindingRepository(session)
        self._audit = AuditService(session)

    async def _require_agent(self, tenant_id: str, agent_id: uuid.UUID) -> AgentDefinition:
        agent = await self._session.get(AgentDefinition, agent_id)
        if agent is None or agent.is_deleted or agent.tenant_id != tenant_id:
            raise AppError(ErrorCode.AGENT_NOT_FOUND)
        return agent

    async def _require_server(self, tenant_id: str, mcp_id: uuid.UUID) -> McpServer:
        server = await self._session.get(McpServer, mcp_id)
        if server is None or server.is_deleted or server.tenant_id != tenant_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "McpServer"})
        return server

    async def list_bindings(
        self, tenant_id: str, agent_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_agent(tenant_id, agent_id)
        rows = await self._bindings.list_for_agent(tenant_id, agent_id)
        items = [binding_item(binding, server) for binding, server in rows]
        start = (page - 1) * page_size
        return items[start : start + page_size], len(items)

    async def bind(
        self, tenant_id: str, agent_id: uuid.UUID, mcp_id: uuid.UUID, actor: AuditActor
    ) -> dict[str, Any]:
        agent = await self._require_agent(tenant_id, agent_id)
        server = await self._require_server(tenant_id, mcp_id)
        binding = await self._bindings.find(agent.id, server.id)
        if binding is not None and not binding.is_deleted:
            return binding_item(binding, server)
        before = binding_snapshot(binding) if binding is not None else None
        if binding is None:
            binding = AgentMcpBinding(agent_id=agent.id, mcp_server_id=server.id)
            await self._bindings.add(binding)
        else:
            binding.is_deleted = False
            binding.update_time = datetime.now(UTC)
            await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=agent.id,
            action="GRANT",
            before=before,
            after=binding_snapshot(binding),
        )
        return binding_item(binding, server)

    async def unbind(
        self, tenant_id: str, agent_id: uuid.UUID, mcp_id: uuid.UUID, actor: AuditActor
    ) -> dict[str, Any]:
        agent = await self._require_agent(tenant_id, agent_id)
        binding = await self._bindings.find(agent.id, mcp_id)
        if binding is None or binding.is_deleted:
            return {"agent_id": str(agent.id), "mcp_server_id": str(mcp_id), "is_deleted": True}
        before = binding_snapshot(binding)
        binding.is_deleted = True
        binding.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=agent.id,
            action="REVOKE",
            before=before,
            after=binding_snapshot(binding),
        )
        return {"agent_id": str(agent.id), "mcp_server_id": str(mcp_id), "is_deleted": True}

    async def effective_mcp_servers(
        self, tenant_id: str, agent_id: uuid.UUID, actor_user_id: uuid.UUID
    ) -> list[ResolvedMcpServer]:
        """EffectiveMcp 公式（design §3.3.1）：在 Prompt/ToolRegistry 前过滤。"""
        rows = await self._session.execute(
            select(AgentMcpBinding, McpServer)
            .join(McpServer, McpServer.id == AgentMcpBinding.mcp_server_id)
            .outerjoin(McpUserGrant, (McpUserGrant.mcp_server_id == McpServer.id) & (McpUserGrant.user_id == actor_user_id))
            .where(
                AgentMcpBinding.agent_id == agent_id,
                AgentMcpBinding.is_deleted.is_(False),
                McpServer.tenant_id == tenant_id,
                McpServer.is_deleted.is_(False),
                McpServer.enabled.is_(True),
            )
        )
        servers: list[ResolvedMcpServer] = []
        for _binding, server in rows.all():
            if server.user_scope != "ALL":
                granted = await self._session.scalar(
                    select(McpUserGrant.id).where(
                        McpUserGrant.mcp_server_id == server.id,
                        McpUserGrant.user_id == actor_user_id,
                        McpUserGrant.is_deleted.is_(False),
                    )
                )
                if granted is None:
                    continue
            servers.append(
                ResolvedMcpServer(
                    mcp_server_id=server.id,
                    key=server.key,
                    endpoint=server.endpoint,
                    catalog_revision=server.tool_catalog_revision,
                    catalog_hash=server.tool_catalog_hash,
                    tools=list(server.tool_catalog_json or []),
                )
            )
        return servers
