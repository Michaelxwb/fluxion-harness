from __future__ import annotations

from collections.abc import Sequence

from fluxion.kernel.events import BeforeToolCallPayload, TypedEventBus
from fluxion.plugins.contracts import ToolCall, ToolDefinition
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.agent import ModelToolResult
from fluxion.runtime.capabilities import EffectiveCapabilityResolver
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.model_providers import RegistryOpenAIModelProvider
from fluxion.runtime.secrets import CredentialResolver
from fluxion.runtime.tools import ToolResult, ToolRuntime
from fluxion.services.runtime_contracts import RuntimeApplicationError, ToolCallRequest
from fluxion.services.runtime_utils import _tool_model_content, _tool_result_payload


class RuntimeToolOps:
    """工具执行操作 mixin：模型工具编排、工具策略解析与 Hook 分发。

    由 RuntimeApplicationService 继承，依赖属性在主类 __init__ 中装配；此处仅
    声明类型，避免 mixin 直接持有构造逻辑。
    """

    _store: RegistryStore
    _tool_runtime: ToolRuntime
    _event_bus: TypedEventBus
    _model_providers: ModelProviderRegistry
    _credential_resolver: CredentialResolver | None

    async def _call_tools(
        self,
        context: RuntimeContext,
        tool_calls: Sequence[ToolCallRequest],
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for call in tool_calls:
            result = await self._call_tool(context, call)
            results.append(_tool_result_payload(call.tool_id, result))
        return results

    def _prepare_registry_model_providers(self, context: RuntimeContext) -> None:
        configured = context.snapshot.model_resolution.get("provider")
        failover = context.snapshot.model_resolution.get("failover", [])
        provider_ids = [configured] if isinstance(configured, str) else []
        if isinstance(failover, list):
            provider_ids.extend(item for item in failover if isinstance(item, str))
        for provider_id in provider_ids:
            if provider_id not in context.snapshot.plugin_versions:
                continue
            self._model_providers.register(
                provider_id,
                RegistryOpenAIModelProvider(
                    provider_id,
                    self._store,
                    self._credential_resolver,
                ),
            )

    async def _execute_model_tool(
        self,
        context: RuntimeContext,
        call: ToolCall,
        *,
        allowed_tool_ids: set[str],
    ) -> ModelToolResult:
        if call.name not in allowed_tool_ids:
            raise RuntimeApplicationError(
                "tool_not_allowed",
                f"model requested unavailable tool {call.name}",
            )
        result = await self._call_tool(
            context,
            ToolCallRequest(tool_id=call.name, arguments=call.arguments),
        )
        payload = _tool_result_payload(call.name, result)
        return ModelToolResult(
            call_id=call.call_id,
            tool_id=call.name,
            content=_tool_model_content(result),
            payload=payload,
        )

    async def _call_tool(
        self,
        context: RuntimeContext,
        call: ToolCallRequest,
    ) -> ToolResult:
        user_tools, agent_tools, tenant_tools = await self._effective_tool_policy(context)
        await self._dispatch_before_tool(context, call)
        return await self._tool_runtime.call(
            context,
            call.tool_id,
            call.arguments,
            user_grants=user_tools,
            agent_allowlist=agent_tools,
            tenant_policy=tenant_tools,
        )

    async def _model_tool_definitions(
        self,
        context: RuntimeContext,
        mcp_tool_ids: set[str],
    ) -> list[ToolDefinition]:
        user_tools, agent_tools, tenant_tools = await self._effective_tool_policy(context)
        descriptors = self._tool_runtime.list_effective_descriptors(
            user_grants=user_tools,
            agent_allowlist=agent_tools,
            tenant_policy=tenant_tools,
        )
        descriptors = [
            descriptor
            for descriptor in descriptors
            if not descriptor.tool_id.startswith("mcp__")
            or descriptor.tool_id in mcp_tool_ids
        ]
        return [
            ToolDefinition(
                name=descriptor.tool_id,
                description=descriptor.name,
                parameters=descriptor.parameters_schema or {"type": "object"},
            )
            for descriptor in descriptors
        ]

    async def _effective_tool_policy(
        self,
        context: RuntimeContext,
    ) -> tuple[set[str], set[str], set[str]]:
        agent_tools = await self._allowed_tools(context)
        capability = EffectiveCapabilityResolver(self._store)
        granted_mcp = await capability.user_granted_tools(
            tenant_id=context.snapshot.tenant_id,
            user_id=context.snapshot.user_id,
            runtime_profile_id=context.snapshot.runtime_profile_id,
        )
        user_tools = agent_tools | granted_mcp
        policy_allowed, configured = await capability.tenant_policy_tools(
            tenant_id=context.snapshot.tenant_id
        )
        tenant_tools = policy_allowed if configured else user_tools
        return user_tools, agent_tools, tenant_tools

    async def _dispatch_before_tool(
        self,
        context: RuntimeContext,
        call: ToolCallRequest,
    ) -> None:
        await self._event_bus.dispatch(
            BeforeToolCallPayload(
                tenant_id=context.snapshot.tenant_id,
                execution_id=context.snapshot.execution_id,
                trace_id=context.snapshot.trace_id,
                tool_id=call.tool_id,
                arguments=dict(call.arguments),
            ),
            trace_sink=context,
        )

    async def _allowed_tools(self, context: RuntimeContext) -> set[str]:
        profile = await self._store.get(
            ResourceKind.RUNTIME_PROFILE,
            context.snapshot.runtime_profile_id,
            tenant_id=context.snapshot.tenant_id,
            version=context.snapshot.runtime_profile_version,
        )
        if profile is None:
            return set()
        raw_tools = profile.spec_json.get("allowed_tools", [])
        if not isinstance(raw_tools, list):
            return set()
        profile_tools = {tool for tool in raw_tools if isinstance(tool, str)}
        return profile_tools | set(context.snapshot.skill_allowed_tools)
