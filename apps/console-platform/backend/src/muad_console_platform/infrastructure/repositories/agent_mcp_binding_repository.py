import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.mcp import AgentMcpBinding, McpServer


class AgentMcpBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(
        self, agent_id: uuid.UUID, mcp_server_id: uuid.UUID
    ) -> AgentMcpBinding | None:
        binding: AgentMcpBinding | None = await self._session.scalar(
            select(AgentMcpBinding).where(
                AgentMcpBinding.agent_id == agent_id,
                AgentMcpBinding.mcp_server_id == mcp_server_id,
            )
        )
        return binding

    async def list_for_agent(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
    ) -> list[tuple[AgentMcpBinding, McpServer]]:
        result = await self._session.execute(
            select(AgentMcpBinding, McpServer)
            .join(McpServer, McpServer.id == AgentMcpBinding.mcp_server_id)
            .where(
                AgentMcpBinding.agent_id == agent_id,
                AgentMcpBinding.is_deleted.is_(False),
                McpServer.tenant_id == tenant_id,
                McpServer.is_deleted.is_(False),
            )
            .order_by(AgentMcpBinding.create_time)
        )
        return [(binding, server) for binding, server in result.all()]

    async def count_active(self, tenant_id: str) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(AgentMcpBinding)
            .join(McpServer, McpServer.id == AgentMcpBinding.mcp_server_id)
            .where(
                McpServer.tenant_id == tenant_id,
                McpServer.is_deleted.is_(False),
                AgentMcpBinding.is_deleted.is_(False),
            )
        )
        return int(total or 0)

    async def add(self, binding: AgentMcpBinding) -> AgentMcpBinding:
        self._session.add(binding)
        await self._session.flush()
        return binding
