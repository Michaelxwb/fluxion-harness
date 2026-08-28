"""Product Agent 应用服务（closure TASK-003 / P1C-08）。

以 ``agent_id`` 为主坐标的产品面用例：产品信息查询与执行发起。agent →
RuntimeProfile 的 mechanics 解析在服务层内聚（复用 AgentDefinitionRepository
的引用合并读取），产品面响应零 runtime_profile_id 泄漏（RULE-C-03）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from fluxion.agents.definitions import AgentDefinition
from fluxion.agents.repository import AgentDefinitionRepository
from fluxion.errors.console import RESOURCE_NOT_FOUND, ConsoleError
from fluxion.registry import ChannelRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RuntimeApplicationService,
    RuntimeStreamEvent,
)


class ProductAgentApplicationService:
    """产品 Agent 门面：GET 产品面 + runs(:stream) 执行入口。"""

    def __init__(self, *, store: ChannelRegistryStore, runtime: RuntimeApplicationService) -> None:
        self._store = store
        self._runtime = runtime
        self._agents = AgentDefinitionRepository(store)

    async def get_agent_face(self, *, tenant_id: str, agent_id: str) -> dict[str, Any]:
        """产品面：displayName/description/能力/可用性；不含任何 mechanics。"""
        agent = await self._store.get(
            ResourceKind.AGENT_DEFINITION, agent_id, tenant_id=tenant_id
        )
        if agent is None:
            raise ConsoleError(RESOURCE_NOT_FOUND, f"agent_not_found: {agent_id}", 404)
        spec = AgentDefinition.model_validate(agent.spec_json)
        return {
            "agent_id": agent.id,
            "name": spec.name,
            "display_name": spec.name,
            "description": spec.description,
            "available": agent.status.value == "published",
            "capabilities": [
                {
                    "type": binding.type.value,
                    "capability_ref": binding.capability_ref,
                    "version_pin": binding.version_pin,
                }
                for binding in spec.capabilities
            ],
        }

    async def run(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        session_id: str,
        input_message: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        runtime_profile_id = await self._resolve_profile_id(tenant_id, agent_id)
        result = await self._runtime.run(
            RunRuntimeRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                runtime_profile_id=runtime_profile_id,
                agent_definition_id=agent_id,
                session_id=session_id,
                input_message=input_message,
                request_id=request_id or f"req_{uuid4().hex}",
                trace_id=trace_id or f"trace_{uuid4().hex}",
            )
        )
        payload = result.to_payload()
        payload.pop("runtime_profile_id", None)
        return payload

    async def stream(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        user_id: str,
        session_id: str,
        input_message: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """流式执行；profile 解析完成后交给 runtime 流。"""
        runtime_profile_id = await self._resolve_profile_id(tenant_id, agent_id)
        request = RunRuntimeRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            runtime_profile_id=runtime_profile_id,
            agent_definition_id=agent_id,
            session_id=session_id,
            input_message=input_message,
            request_id=request_id or f"req_{uuid4().hex}",
            trace_id=trace_id or f"trace_{uuid4().hex}",
        )
        async for event in self._runtime.stream(request):
            yield event

    async def _resolve_profile_id(self, tenant_id: str, agent_id: str) -> str:
        """mechanics 解析：Agent.runtime_profile_ref；缺省同名回退（fixture/迁移约定）。"""
        agent, profile = await self._agents.resolve(
            tenant_id=tenant_id, resource_id=agent_id
        )
        del agent
        if profile is not None:
            return profile.id
        return agent_id

