from __future__ import annotations

from collections.abc import Sequence

from fluxion.kernel.events import BeforeToolCallPayload, TypedEventBus
from fluxion.plugins.contracts import ToolCall, ToolDescriptor
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind, ResourceStatus
from fluxion.runtime.agent import ModelToolResult
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.model_providers import (
    RegistryOpenAIModelProvider,
    ScopedModelProviderResolver,
)
from fluxion.runtime.secrets import CredentialResolver
from fluxion.runtime.tools import ToolResult, ToolRuntime, frozen_tool_policy
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

    async def _prepare_execution_model_resolver(
        self, context: RuntimeContext
    ) -> ScopedModelProviderResolver:
        # TASK-010：execution-scoped Provider Resolver——不 mutate service-level registry，
        # store-backed provider 只叠加在本执行副本上，执行结束随 context GC。
        resolver = ScopedModelProviderResolver(self._model_providers)
        policy = context.snapshot.model_resolution
        for route in policy.routes:
            provider_id = route.provider_ref.id
            # 双重门槛：①在 snapshot.plugin_versions 中被 ADR-A008 三层解析
            # （model_policy → ModelDefinition → provider_ref）显式 pin；
            # ②Registry 存在同 id 的 MODEL_PROVIDER 资源。两者同时满足才包装为
            # store-backed 注册——否则保留进程内已注册实现（DevEcho/测试桩等）。
            if provider_id not in context.snapshot.plugin_versions:
                continue
            # ADR-A008（TASK-002）：运行时从 Registry MODEL_PROVIDER 资源解析 provider，
            # 不再以 PLUGIN(model_provider) 作为模型事实源。
            provider = await self._store.get(
                ResourceKind.MODEL_PROVIDER,
                provider_id,
                tenant_id=context.snapshot.tenant_id,
                version=route.provider_ref.version,
            )
            if provider is None or provider.status is not ResourceStatus.PUBLISHED:
                raise RuntimeApplicationError(
                    "model_provider_unavailable",
                    f"model provider {provider_id}@{route.provider_ref.version} is unavailable",
                )
            if provider_id in self._model_providers.provider_ids():
                continue
            resolver.register_scoped(
                provider_id,
                RegistryOpenAIModelProvider(
                    provider_id,
                    self._store,
                    self._credential_resolver,
                ),
            )
        return resolver

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

    def _execution_tool_runtime(self, context: RuntimeContext) -> ToolRuntime:
        # F4：优先 per-execution 隔离 runtime（run/stream 起始 clone 自 base，
        # builtin/注入工具已拷入、MCP prepare 注入副本）。context.tool_runtime
        # 未设（直接调 mixin 的边界路径）回退 service-level base，保留既有行为。
        return context.tool_runtime or self._tool_runtime

    async def _call_tool(
        self,
        context: RuntimeContext,
        call: ToolCallRequest,
    ) -> ToolResult:
        user_tools, agent_tools, tenant_tools = frozen_tool_policy(context, context.mcp_tool_ids)
        # A22：授权（与存在性）先于 hook 分发。frozen 图三元组预检，未通过直接拒绝。
        self._execution_tool_runtime(context).descriptor(call.tool_id)
        if (
            call.tool_id not in user_tools
            or call.tool_id not in agent_tools
            or call.tool_id not in tenant_tools
        ):
            raise RuntimeApplicationError(
                "tool_not_allowed", f"tool {call.tool_id} is not allowed"
            )
        await self._dispatch_before_tool(context, call)
        return await self._execution_tool_runtime(context).call(
            context,
            call.tool_id,
            call.arguments,
            mcp_tool_ids=context.mcp_tool_ids,
        )

    async def _model_tool_definitions(
        self,
        context: RuntimeContext,
        mcp_tool_ids: set[str],
    ) -> list[ToolDescriptor]:
        descriptors = self._execution_tool_runtime(context).list_effective_descriptors(
            context, mcp_tool_ids=mcp_tool_ids
        )
        descriptors = [
            descriptor
            for descriptor in descriptors
            if not descriptor.tool_id.startswith("mcp__")
            or descriptor.tool_id in mcp_tool_ids
        ]
        return [
            ToolDescriptor(
                name=descriptor.tool_id,
                description=descriptor.name,
                parameters=descriptor.parameters_schema or {"type": "object"},
            )
            for descriptor in descriptors
        ]

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
