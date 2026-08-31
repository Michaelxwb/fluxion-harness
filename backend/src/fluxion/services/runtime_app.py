from __future__ import annotations

import json
import traceback
from collections import deque
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
from fluxion.observability.context import bind_execution_id, reset_execution_id
from fluxion.observability.logging import emit_runtime_error_log
from fluxion.observability.tracing import traced_scope
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import (
    ChannelRegistryStore,
    RegistryStoreError,
)
from fluxion.resources import (
    ResourceDefinition,
    ResourceKind,
    TenantResourceCache,
)
from fluxion.runtime.agent import AgentRuntime, RuntimeStepResult
from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.hot_reload import ConfigChangeEvent, RevisionAwareResourceResolver
from fluxion.runtime.mcp import RegistryMCPRuntime
from fluxion.runtime.memory import SessionMemoryStore
from fluxion.runtime.secrets import CredentialResolver
from fluxion.runtime.tools import ToolRuntime
from fluxion.runtime.tracing import InMemoryTraceStore, TraceRecord, TraceStore
from fluxion.services.context_resolver import ContextResolver, ContextResolverSnapshotBuilder
from fluxion.services.execution_session import ExecutionSession
from fluxion.services.outbox import InProcessConfigEventPublisher, OutboxWorker
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
from fluxion.services.runtime_profile_service import RuntimeProfileService
from fluxion.services.runtime_tool_ops import RuntimeToolOps
from fluxion.services.runtime_utils import (
    DevEchoModelProvider,
    _derive_plugin_summaries,
    _elapsed_ms,
    _error_code,
    _hook_events,
    _last_event_attrs,
    _run_result,
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
        store: ChannelRegistryStore,
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
        self._profile_service = RuntimeProfileService(
            store, on_config_changed=self.handle_config_changed
        )
        self._model_providers = model_providers or ModelProviderRegistry()
        self._credential_resolver = credential_resolver
        self._runtime = AgentRuntime(
            snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(store)),
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
        # F10：config change event 环形缓冲（长跑进程此前无界 append → OOM）。
        # 仅 dev 观测用途（config_events 属性 + 测试读 [-1]），maxlen 覆盖最近
        # 变更窗口即可；超出自动丢弃最旧。
        self._config_events: deque[ConfigChangeEvent] = deque(maxlen=1000)

    @classmethod
    def create_dev_bundle(
        cls,
        store: ChannelRegistryStore,
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
        # closure TASK-011：用户自助工具注册（对话即界面——profile/preference/memory）
        from fluxion.memory.application.user_tools import register_user_tools
        from fluxion.users.service import UserDomainService

        register_user_tools(tool_runtime, engine=store.engine, users=UserDomainService(store))
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
        return await self._profile_service.create_runtime_profile(request)

    async def publish_runtime_profile(
        self,
        request: PublishRuntimeProfileRequest,
    ) -> ResourceDefinition:
        return await self._profile_service.publish_runtime_profile(request)

    async def ensure_runtime_profile(
        self,
        request: CreateRuntimeProfileRequest,
    ) -> ResourceDefinition:
        return await self._profile_service.ensure_runtime_profile(request)

    async def run(self, request: RunRuntimeRequest) -> RunRuntimeResult:
        started = perf_counter()
        context: RuntimeContext | None = None
        step_result: RuntimeStepResult | None = None
        tool_results: list[dict[str, object]] = []
        # O502（TASK-008）：Runtime execution span 经 traced_scope——统一关联字段；
        # 同时绑定 execution_id ContextVar，使嵌套 Model/Tool span 自动继承。
        execution_token = bind_execution_id(request.execution_id)
        try:
            async with traced_scope(
                "runtime.execution",
                attributes={
                    "fluxion.runtime_profile_id": request.runtime_profile_id,
                },
            ) as span:
                try:
                    prepared = await ExecutionSession(self).prepare(request)
                    context = prepared.context
                    model_tools = prepared.model_tools
                    allowed_model_tools = prepared.allowed_model_tools
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
        finally:
            reset_execution_id(execution_token)

    async def stream(self, request: RunRuntimeRequest) -> AsyncIterator[RuntimeStreamEvent]:
        # review P1-4：流式主路径（Chat Channel 正式入口）此前不 bind
        # execution_id、无 runtime.execution span → 流式执行中嵌套 Model/Tool
        # span 缺 fluxion.execution_id（E-03 四字段门禁在主 Chat 路径不达标）。
        # 与 run() 对齐：ContextVar 绑定 + O502 span（mode=stream 标记）。
        execution_token = bind_execution_id(request.execution_id)
        try:
            async with traced_scope(
                "runtime.execution",
                attributes={
                    "fluxion.runtime_profile_id": request.runtime_profile_id,
                    "fluxion.execution.mode": "stream",
                },
            ):
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
        finally:
            reset_execution_id(execution_token)

    async def _stream_tokens_or_fallback(
        self, request: RunRuntimeRequest
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """无工具场景且模型支持流式时逐 token 输出，否则回退到非流式 run。

        与 run() 对齐的异常契约：流式专属路径的异常被收口为 RuntimeApplicationError
        并补 trace + error log；仅当 provider 不支持流式（返回空、不抛错）时才回退
        到非流式 run（单次模型调用）。此前任何异常都被吞成 chunks=[] 再回退 run()，
        导致流式失败被静默重试（双倍模型调用 + 错误永不暴露）。
        """
        started = perf_counter()
        context: RuntimeContext | None = None
        try:
            prepared = await ExecutionSession(self).prepare(request)
            context = prepared.context
            model_tools = prepared.model_tools
            if model_tools:
                # 有工具可用：模型可能发起 tool call，须走完整非流式循环。复用已
                # start 的 context——此前 finish 后再 run(request) 会重开第二个
                # context，首个 context 的 trace 被丢弃且 mcp/模型定义重复准备。
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
                tool_results = list(step_result.tool_results)
                tool_results.extend(await self._call_tools(context, request.tool_calls))
                await self._runtime.finish_execution(context)
                latency_ms = _elapsed_ms(started)
                await self._append_trace(
                    context, step_result, tuple(tool_results), latency_ms, None
                )
                yield RuntimeStreamEvent(
                    event="completed",
                    data=_run_result(
                        request,
                        context,
                        step_result,
                        tuple(tool_results),
                        latency_ms,
                        self._service_instance_id,
                    ).to_payload(),
                )
                return
            chunks: list[str] = []
            async for token in self._runtime.stream_final_answer(
                context, request.input_message
            ):
                chunks.append(token)
                yield RuntimeStreamEvent(event="token", data={"content": token})
            if chunks:
                output = "".join(chunks)
                await self._runtime.memory.add_message(context, "user", request.input_message)
                await self._runtime.memory.add_message(context, "assistant", output)
                await self._runtime.finish_execution(context)
                latency_ms = _elapsed_ms(started)
                # 此前流式成功分支只 yield completed、从不 append_trace，
                # 流式执行在 trace_store 中完全不可观测。
                await self._append_trace(context, None, (), latency_ms, None)
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
                        "latency_ms": latency_ms,
                        "model_provider_id": None,
                        "tool_results": [],
                    },
                )
                return
            # 流式不被支持（provider 非 StreamingModelProvider / 无 provider）→
            # 回退非流式 run（单次模型调用）。context 已置 None，run() 会自起 context
            # 并自负 error log + trace + RuntimeApplicationError 包装。
            await self._runtime.finish_execution(context)
            context = None
            result = await self.run(request)
            yield RuntimeStreamEvent(event="completed", data=result.to_payload())
        except RuntimeApplicationError:
            # 来自 fallback 的 run()：run() 自身已完成 error log + trace + 包装，直接上抛。
            raise
        except Exception as exc:
            if context is not None:
                context.emit("execution.error", {"error": _error_code(exc)})
                with suppress(Exception):
                    await self._runtime.finish_execution(context)
                await self._append_trace(
                    context,
                    None,
                    (),
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

    def build_outbox_worker(self) -> OutboxWorker:
        # A7：为 serve lifespan 提供 outbox drain worker。publisher 路由到
        # handle_config_changed（push-invalidation，与 revision 轮询按 revision
        # 收敛共存）。仅 lifespan 调 start/stop——不进 initialize()，避免测试中
        # 后台 worker drain 掉断言所依赖的 PENDING 行。
        return OutboxWorker(
            self._store,
            InProcessConfigEventPublisher(self.handle_config_changed),
            worker_id=self._service_instance_id,
        )

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
