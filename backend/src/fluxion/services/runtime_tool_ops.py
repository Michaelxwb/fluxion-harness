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
        user_tools, agent_tools, tenant_tools = await self._effective_tool_policy(context)
        # A22：授权（与存在性）先于 hook 分发。设计要求
        # CheckPolicy→CheckGrant→Allowlist→BeforeToolHooks；此前
        # _dispatch_before_tool 在 ToolRuntime.call 的三重交集之前分发，DLP/安全
        # hook 会看到用户本无权调用的工具参数，且 fail_closed hook 可在授权结论
        # 产生前中断。此处做与 ToolRuntime.call 一致的预检，未通过则直接拒绝，
        # 不进入 hook、不执行。
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
        descriptors = self._execution_tool_runtime(context).list_effective_descriptors(
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
        # A2/ADR-005：每执行期首次解析后缓存于 context.tool_policy，后续 tool call
        # 与 model tool 列表构建复用同一结果——消除执行期版本漂移（此前每次调用
        # 新建 EffectiveCapabilityResolver 按 latest-published 实时解析，执行中途
        # 租户发布新 Policy 会令后半段授权集合变化、与 trace 记录的 snapshot 版本
        # 不一致）与每个 tool call 的 N+1 查询。
        if context.tool_policy is not None:
            return context.tool_policy
        agent_tools = await self._allowed_tools(context)
        capability = EffectiveCapabilityResolver(self._store)
        granted_mcp = await capability.user_granted_tools(
            tenant_id=context.snapshot.tenant_id,
            user_id=context.snapshot.user_id,
            runtime_profile_id=context.snapshot.runtime_profile_id,
        )
        user_tools = agent_tools | granted_mcp
        policy_allowed, policy_denied, configured = await capability.tenant_policy_tools(
            tenant_id=context.snapshot.tenant_id
        )
        if not configured:
            tenant_tools = set(user_tools)
        elif policy_allowed:
            # allow-list 模式：tenant 显式限定可用集合。
            tenant_tools = set(policy_allowed)
        else:
            # deny-only 模式（allowed 为空）：不缩小集合，仅靠 denied 移除。
            # 此前此分支把 tenant_tools 置空 → tenant 所有工具被锁死。
            tenant_tools = set(user_tools)
        # denied 始终优先：从所有维度移除，确保 ToolRuntime 三重交集不会
        # 放行 tenant 显式拒绝的工具（此前 denied 被直接丢弃，安全洞）。
        if policy_denied:
            user_tools = user_tools - policy_denied
            agent_tools = agent_tools - policy_denied
            tenant_tools = tenant_tools - policy_denied
        policy = (user_tools, agent_tools, tenant_tools)
        context.tool_policy = policy
        return policy

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
