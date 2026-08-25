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

from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from fluxion.kernel.events import TypedEventBus
from fluxion.observability.logging import emit_runtime_error_log
from fluxion.observability.tracing import get_tracer
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import RegistryStore, RegistryStoreError
from fluxion.resources import (
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    TenantResourceCache,
)
from fluxion.runtime.agent import AgentRuntime, RuntimeStepResult
from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.hot_reload import ConfigChangeEvent, RevisionAwareResourceResolver
from fluxion.runtime.mcp import RegistryMCPRuntime
from fluxion.runtime.memory import SessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder
from fluxion.runtime.secrets import CredentialResolver
from fluxion.runtime.tools import ToolRuntime
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
from fluxion.services.runtime_tool_ops import RuntimeToolOps
from fluxion.services.runtime_utils import (
    DevEchoModelProvider,
    _derive_plugin_summaries,
    _elapsed_ms,
    _error_code,
    _hook_events,
    _last_event_attrs,
    _request_context,
    _run_result,
    _runtime_profile_spec,
    _tool_events,
    default_session_memory_store,
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


class RuntimeApplicationService(RuntimeToolOps):
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
            memory_store=memory_store or default_session_memory_store(store),
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
        tracer = get_tracer("fluxion.runtime")
        with tracer.start_as_current_span(
            "runtime.execute",
            attributes={
                "fluxion.trace_id": request.trace_id,
                "fluxion.request_id": request.request_id,
                "fluxion.execution_id": request.execution_id,
                "fluxion.tenant_id": request.tenant_id,
                "fluxion.runtime_profile_id": request.runtime_profile_id,
            },
        ) as span:
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
                await self._append_trace(
                    context, step_result, tuple(tool_results), latency_ms, None
                )
                return _run_result(
                    request,
                    context,
                    step_result,
                    tuple(tool_results),
                    latency_ms,
                    self._service_instance_id,
                )
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
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
        async for event in self._stream_tokens_or_fallback(request):
            yield event

    async def _stream_tokens_or_fallback(
        self, request: RunRuntimeRequest
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """无工具场景且模型支持流式时逐 token 输出，否则回退到非流式 run。"""
        started = perf_counter()
        context = await self._runtime.start_execution(_request_context(request))
        self._prepare_registry_model_providers(context)
        mcp_tool_ids = await self._mcp_runtime.prepare(context, self._tool_runtime)
        model_tools = await self._model_tool_definitions(context, mcp_tool_ids)
        if model_tools:
            # 有工具可用，模型可能发起 tool call，必须走完整非流式循环。
            with suppress(Exception):
                await self._runtime.finish_execution(context)
            result = await self.run(request)
            yield RuntimeStreamEvent(event="completed", data=result.to_payload())
            return
        chunks: list[str] = []
        try:
            async for token in self._runtime.stream_final_answer(context, request.input_message):
                chunks.append(token)
                yield RuntimeStreamEvent(event="token", data={"content": token})
        except Exception:  # noqa: BLE001 - 流式失败须回退到非流式 run，不中断执行
            chunks = []
        if chunks:
            output = "".join(chunks)
            await self._runtime.memory.add_message(context, "user", request.input_message)
            await self._runtime.memory.add_message(context, "assistant", output)
            await self._runtime.finish_execution(context)
            yield RuntimeStreamEvent(
                event="completed",
                data={
                    "request_id": request.request_id,
                    "trace_id": context.snapshot.trace_id,
                    "execution_id": context.snapshot.execution_id,
                    "service_instance_id": self._service_instance_id,
                    "runtime_profile_id": context.snapshot.runtime_profile_id,
                    "runtime_profile_version": context.snapshot.runtime_profile_version,
                    "output": output,
                    "latency_ms": _elapsed_ms(started),
                    "model_provider_id": None,
                    "tool_results": [],
                },
            )
            return
        with suppress(Exception):
            await self._runtime.finish_execution(context)
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
