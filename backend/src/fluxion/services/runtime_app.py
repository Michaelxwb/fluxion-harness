from __future__ import annotations

import json
import traceback
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from functools import partial
from json import JSONDecodeError
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from fluxion.kernel.events import BeforeToolCallPayload, TypedEventBus
from fluxion.observability.logging import emit_runtime_error_log
from fluxion.plugins.contracts import ModelRequest, ModelResponse, ToolCall, ToolDefinition
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import RegistryStore, RegistryStoreError
from fluxion.resources import (
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
    TenantResourceCache,
)
from fluxion.runtime.agent import AgentRuntime, ModelToolResult, RuntimeStepResult
from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
from fluxion.runtime.capabilities import EffectiveCapabilityResolver
from fluxion.runtime.context import RequestContext, RuntimeContext, TraceEvent
from fluxion.runtime.hot_reload import ConfigChangeEvent, RevisionAwareResourceResolver
from fluxion.runtime.mcp import RegistryMCPRuntime
from fluxion.runtime.memory import InMemorySessionMemoryStore, SessionMemoryStore
from fluxion.runtime.model_providers import RegistryOpenAIModelProvider
from fluxion.runtime.resolver import ExecutionSnapshotBuilder
from fluxion.runtime.secrets import CredentialResolver
from fluxion.runtime.tools import ToolResult, ToolRuntime
from fluxion.runtime.tracing import InMemoryTraceStore, TraceRecord, TraceStore
from fluxion.services.runtime_contracts import (
    CreateRuntimeProfileRequest,
    HealthResult,
    PluginSummary,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RunRuntimeResult,
    RuntimeApplicationError,
    RuntimeStreamEvent,
    ToolCallRequest,
    default_runtime_profile_request,
)

__all__ = [
    "CreateRuntimeProfileRequest",
    "HealthResult",
    "PluginSummary",
    "PublishRuntimeProfileRequest",
    "RunRuntimeRequest",
    "RunRuntimeResult",
    "RuntimeApplicationError",
    "RuntimeApplicationService",
    "RuntimeStreamEvent",
    "ToolCallRequest",
    "default_runtime_profile_request",
]


class DevEchoModelProvider:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        content = request.messages[-1].content if request.messages else ""
        model = request.model or "dev"
        return ModelResponse(provider_id="dev.echo", content=f"{model}: {content}")


class RuntimeApplicationService:
    def __init__(
        self,
        store: RegistryStore,
        *,
        cache_ttl_seconds: float = 60.0,
        trace_store: TraceStore | None = None,
        model_providers: ModelProviderRegistry | None = None,
        tool_runtime: ToolRuntime | None = None,
        event_bus: TypedEventBus | None = None,
        memory_store: SessionMemoryStore | None = None,
        mcp_runtime: RegistryMCPRuntime | None = None,
        credential_resolver: CredentialResolver | None = None,
        plugin_summaries: Sequence[PluginSummary] = (),
    ) -> None:
        self._store = store
        self._cache = TenantResourceCache(ttl_seconds=cache_ttl_seconds)
        self._resolver = RevisionAwareResourceResolver(store, cache=self._cache)
        self._model_providers = model_providers or ModelProviderRegistry()
        self._credential_resolver = credential_resolver
        self._runtime = AgentRuntime(
            snapshot_builder=ExecutionSnapshotBuilder(self._resolver),
            memory_store=memory_store or InMemorySessionMemoryStore(),
            model_providers=self._model_providers,
        )
        self._trace_store = trace_store or InMemoryTraceStore()
        self._tool_runtime = tool_runtime or ToolRuntime()
        self._event_bus = event_bus or TypedEventBus()
        self._mcp_runtime = mcp_runtime or RegistryMCPRuntime(
            store,
            credential_resolver=credential_resolver,
        )
        self._plugin_summaries = tuple(plugin_summaries)
        self._service_instance_id = uuid4().hex
        self._config_events: list[ConfigChangeEvent] = []

    @classmethod
    def create_dev_bundle(
        cls,
        store: RegistryStore,
        *,
        cache_ttl_seconds: float = 60.0,
        trace_store: TraceStore | None = None,
        event_bus: TypedEventBus | None = None,
        memory_store: SessionMemoryStore | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> RuntimeApplicationService:
        model_registry = ModelProviderRegistry()
        model_registry.register("dev.echo", DevEchoModelProvider())
        tool_runtime = ToolRuntime()
        register_builtin_tools(tool_runtime, BuiltinToolConfig())
        return cls(
            store,
            cache_ttl_seconds=cache_ttl_seconds,
            trace_store=trace_store,
            model_providers=model_registry,
            tool_runtime=tool_runtime,
            event_bus=event_bus,
            memory_store=memory_store,
            credential_resolver=credential_resolver,
            plugin_summaries=_derive_plugin_summaries(model_registry),
        )

    @property
    def service_instance_id(self) -> str:
        return self._service_instance_id

    @property
    def plugin_summaries(self) -> tuple[PluginSummary, ...]:
        return self._plugin_summaries

    @property
    def trace_store(self) -> TraceStore:
        return self._trace_store

    @property
    def config_events(self) -> tuple[ConfigChangeEvent, ...]:
        return tuple(self._config_events)

    async def initialize(self) -> None:
        await self._store.initialize()

    async def close(self) -> None:
        await self._mcp_runtime.close()
        await self._store.close()

    async def create_runtime_profile(
        self,
        request: CreateRuntimeProfileRequest,
    ) -> ResourceDefinition:
        definition = ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id=request.runtime_profile_id,
            tenant_id=request.tenant_id,
            version=request.version,
            status=ResourceStatus.DRAFT,
            spec_json=_runtime_profile_spec(request),
        )
        return await self._store.put(definition)

    async def publish_runtime_profile(
        self,
        request: PublishRuntimeProfileRequest,
    ) -> ResourceDefinition:
        published = await self._store.publish(
            ResourceKind.RUNTIME_PROFILE,
            request.runtime_profile_id,
            tenant_id=request.tenant_id,
            version=request.version,
        )
        revision = await self._store.bump_revision(tenant_id=request.tenant_id)
        event = ConfigChangeEvent(
            tenant_id=request.tenant_id,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id=request.runtime_profile_id,
            version=request.version,
            revision=revision,
        )
        if request.notify_runtime:
            self.handle_config_changed(event)
        return published

    async def ensure_runtime_profile(
        self,
        request: CreateRuntimeProfileRequest,
    ) -> ResourceDefinition:
        existing = await self._store.get(
            ResourceKind.RUNTIME_PROFILE,
            request.runtime_profile_id,
            tenant_id=request.tenant_id,
            version=request.version,
        )
        if existing is None:
            existing = await self.create_runtime_profile(request)
        if existing.status is ResourceStatus.PUBLISHED:
            return existing
        return await self.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id=request.tenant_id,
                runtime_profile_id=request.runtime_profile_id,
                version=request.version,
            )
        )

    async def run(self, request: RunRuntimeRequest) -> RunRuntimeResult:
        started = perf_counter()
        context: RuntimeContext | None = None
        step_result: RuntimeStepResult | None = None
        tool_results: list[dict[str, object]] = []
        try:
            context = await self._runtime.start_execution(_request_context(request))
            self._prepare_registry_model_providers(context)
            mcp_tool_ids = await self._mcp_runtime.prepare(context, self._tool_runtime)
            model_tools = await self._model_tool_definitions(context, mcp_tool_ids)
            allowed_model_tools = {tool.name for tool in model_tools}
            step_result = await self._runtime.run_step(
                context,
                request.input_message,
                tools=model_tools,
                tool_handler=partial(
                    self._execute_model_tool,
                    allowed_tool_ids=allowed_model_tools,
                ),
            )
            tool_results.extend(step_result.tool_results)
            tool_results.extend(await self._call_tools(context, request.tool_calls))
            await self._runtime.finish_execution(context)
            latency_ms = _elapsed_ms(started)
            await self._append_trace(context, step_result, tuple(tool_results), latency_ms, None)
            return _run_result(
                request,
                context,
                step_result,
                tuple(tool_results),
                latency_ms,
                self._service_instance_id,
            )
        except Exception as exc:
            if context is not None:
                context.emit("execution.error", {"error": _error_code(exc)})
                with suppress(Exception):
                    await self._runtime.finish_execution(context)
                await self._append_trace(
                    context,
                    step_result,
                    tuple(tool_results),
                    _elapsed_ms(started),
                    str(exc),
                )
            emit_runtime_error_log(
                request_id=request.request_id,
                trace_id=request.trace_id,
                tenant_id=request.tenant_id,
                execution_id=request.execution_id,
                runtime_profile_id=request.runtime_profile_id,
                error_type=type(exc).__name__,
                error_code=_error_code(exc),
                message=str(exc),
                stack=traceback.format_exc(),
            )
            raise RuntimeApplicationError(_error_code(exc), str(exc)) from exc

    async def stream(self, request: RunRuntimeRequest) -> AsyncIterator[RuntimeStreamEvent]:
        yield RuntimeStreamEvent(
            event="started",
            data={
                "request_id": request.request_id,
                "execution_id": request.execution_id,
                "runtime_profile_id": request.runtime_profile_id,
                "version_selector": request.runtime_profile_version_selector,
            },
        )
        result = await self.run(request)
        yield RuntimeStreamEvent(event="completed", data=result.to_payload())

    async def validate_resource_file(self, path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            definition = ResourceDefinition.model_validate(payload)
        except (OSError, JSONDecodeError, ValidationError) as exc:
            raise RuntimeApplicationError("resource_validation_failed", str(exc)) from exc
        return {
            "valid": True,
            "kind": definition.kind.value,
            "resource_id": definition.id,
            "version": definition.version,
            "tenant_id": definition.tenant_id,
        }

    def list_plugins(self) -> list[PluginSummary]:
        return list(self._plugin_summaries)

    async def health(self) -> HealthResult:
        return HealthResult("ok", self._service_instance_id)

    async def ready(self) -> HealthResult:
        try:
            await self._store.get(
                ResourceKind.RUNTIME_PROFILE,
                "_ready",
                tenant_id="_ready",
                version="_ready",
            )
        except RegistryStoreError as exc:
            raise RuntimeApplicationError(
                "runtime_not_ready", str(exc), status_code=503
            ) from exc
        return HealthResult("ok", self._service_instance_id)

    def last_seen_revision(self, tenant_id: str) -> int:
        return self._resolver.last_seen_revision(tenant_id)

    def handle_config_changed(self, event: ConfigChangeEvent) -> None:
        self._resolver.handle_config_changed(event)
        self._config_events.append(event)

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

    async def _append_trace(
        self,
        context: RuntimeContext,
        step_result: RuntimeStepResult | None,
        tool_results: tuple[dict[str, object], ...],
        latency_ms: float,
        error: str | None,
    ) -> None:
        events = tuple(context.trace)
        await self._trace_store.append(
            TraceRecord(
                trace_id=context.snapshot.trace_id,
                execution_id=context.snapshot.execution_id,
                tenant_id=context.snapshot.tenant_id,
                runtime_profile_id=context.snapshot.runtime_profile_id,
                runtime_profile_version=context.snapshot.runtime_profile_version,
                snapshot=context.snapshot,
                events=events,
                latency_ms=latency_ms,
                error=error,
                model=_last_event_attrs(events, "model.completed"),
                tools=tool_results or _tool_events(events),
                hooks=_hook_events(events),
            )
        )


def _runtime_profile_spec(request: CreateRuntimeProfileRequest) -> dict[str, object]:
    profile = RuntimeProfile(
        id=request.runtime_profile_id,
        version=request.version,
        prompt=request.prompt,
        model_policy=dict(request.model_policy),
        allowed_skills=list(request.allowed_skills),
        allowed_mcps=list(request.allowed_mcps),
        allowed_tools=list(request.allowed_tools),
        allowed_workflows=list(request.allowed_workflows),
    )
    return {
        "prompt": profile.prompt,
        "model_policy": profile.model_policy,
        "allowed_skills": profile.allowed_skills,
        "allowed_mcps": profile.allowed_mcps,
        "allowed_tools": profile.allowed_tools,
        "allowed_workflows": profile.allowed_workflows,
        "plugin_bindings": profile.plugin_bindings,
    }


def _request_context(request: RunRuntimeRequest) -> RequestContext:
    return RequestContext(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        runtime_profile_id=request.runtime_profile_id,
        session_id=request.session_id,
        runtime_profile_version_selector=request.runtime_profile_version_selector,
        request_id=request.request_id,
        trace_id=request.trace_id,
        execution_id=request.execution_id,
    )


def _run_result(
    request: RunRuntimeRequest,
    context: RuntimeContext,
    step_result: RuntimeStepResult,
    tool_results: tuple[dict[str, object], ...],
    latency_ms: float,
    service_instance_id: str,
) -> RunRuntimeResult:
    model_response = step_result.model_response
    provider_id = model_response.provider_id if model_response is not None else None
    return RunRuntimeResult(
        request_id=request.request_id,
        trace_id=context.snapshot.trace_id,
        execution_id=context.snapshot.execution_id,
        service_instance_id=service_instance_id,
        runtime_profile_id=context.snapshot.runtime_profile_id,
        runtime_profile_version=context.snapshot.runtime_profile_version,
        output=step_result.output,
        latency_ms=latency_ms,
        model_provider_id=provider_id,
        tool_results=tool_results,
    )


def _tool_result_payload(tool_id: str, result: ToolResult) -> dict[str, object]:
    payload: dict[str, object] = {"tool_id": tool_id, "status": result.status.value}
    if result.result is not None:
        payload["result"] = result.result
    if result.run_id is not None:
        payload["run_id"] = result.run_id
    if result.started_status is not None:
        payload["started_status"] = result.started_status
    if result.events is not None:
        payload["events"] = result.events
    if result.policy_decision_id is not None:
        payload["policy_decision_id"] = result.policy_decision_id
    return payload


def _tool_model_content(result: ToolResult) -> str:
    if result.result is not None:
        value: object = result.result
    elif result.events is not None:
        value = {"status": result.status.value, "events": result.events}
    else:
        value = {
            "status": result.status.value,
            "run_id": result.run_id,
            "started_status": result.started_status,
        }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _last_event_attrs(events: tuple[TraceEvent, ...], name: str) -> dict[str, object] | None:
    for event in reversed(events):
        if event.name == name:
            return dict(event.attributes)
    return None


def _tool_events(events: tuple[TraceEvent, ...]) -> tuple[dict[str, object], ...]:
    names = {"tool.completed", "tool.started", "tool.streamed"}
    return tuple(dict(event.attributes) for event in events if event.name in names)


def _hook_events(events: tuple[TraceEvent, ...]) -> tuple[dict[str, object], ...]:
    return tuple(dict(event.attributes) for event in events if event.name.startswith("hook."))


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", RuntimeApplicationError.code)
    return code if isinstance(code, str) else RuntimeApplicationError.code


def _derive_plugin_summaries(model_registry: ModelProviderRegistry) -> tuple[PluginSummary, ...]:
    return tuple(
        PluginSummary(provider_id, "model_provider", "trusted", "in_process")
        for provider_id in model_registry.provider_ids()
    )
