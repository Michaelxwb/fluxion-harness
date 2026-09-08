from __future__ import annotations

import asyncio
import json
import os
import traceback
from collections import deque
from collections.abc import AsyncGenerator, Sequence
from contextlib import suppress
from functools import partial
from json import JSONDecodeError
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from fluxion.kernel.events import TypedEventBus
from fluxion.memory.domain.personal_memory import PersonalMemoryRetriever
from fluxion.observability.context import bind_execution_id, reset_execution_id
from fluxion.observability.logging import emit_runtime_error_log
from fluxion.observability.tracing import traced_scope
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.plugins.providers.pgvector_semantic import PgVectorSemanticStore
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
from fluxion.services.execution_session import ExecutionSession, PreparedExecution
from fluxion.services.outbox import InProcessConfigEventPublisher, OutboxWorker
from fluxion.services.runtime_contracts import (
    TRACE_WRITE_BUDGET_MS,
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
    "build_personal_memory_retriever",
    "default_runtime_profile_request",
    "memory_recall_timeout_from_env",
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
        memory_retriever: PersonalMemoryRetriever | None = None,
        ready_timeout_seconds: float = 1.0,
        memory_recall_timeout_ms: int = 1000,
    ) -> None:
        self._store = store
        self._cache = TenantResourceCache(ttl_seconds=cache_ttl_seconds)
        self._resolver = RevisionAwareResourceResolver(store, cache=self._cache)
        self._profile_service = RuntimeProfileService(
            store, on_config_changed=self.handle_config_changed
        )
        self._model_providers = model_providers or ModelProviderRegistry()
        self._credential_resolver = credential_resolver
        # ADR-A003 amend（TASK-005）：composition root 注入 credential_resolver
        # 与 memory_retriever——ContextResolver 不再裸构造，credential_versions
        # 真实化（非占位 "1"）、memory manifest 经注入 retriever 而非 unavailable。
        self._runtime = AgentRuntime(
            snapshot_builder=ContextResolverSnapshotBuilder(
                ContextResolver(
                    store,
                    credential_resolver=credential_resolver,
                    memory_retriever=memory_retriever,
                    memory_recall_timeout_ms=memory_recall_timeout_ms,
                )
            ),
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
        # FEAT-07：memory provider 引用（engine 由 store 持有并关闭，此处仅引用）。
        self._memory_provider = memory_retriever.provider if memory_retriever is not None else None
        # FEAT-05：readiness 检测预算（默认 1s、无重试），必须小于探针超时；
        # 覆盖 Registry 连接/查询超时与故障，统一 503。
        self._ready_timeout_seconds = ready_timeout_seconds
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
        memory_retriever: PersonalMemoryRetriever | None = None,
        ready_timeout_seconds: float = 1.0,
        memory_recall_timeout_ms: int = 1000,
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
            memory_retriever=memory_retriever,
            ready_timeout_seconds=ready_timeout_seconds,
            memory_recall_timeout_ms=memory_recall_timeout_ms,
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
        # 105 P1-02（TASK-005）：启动时自动安装 entry_points Hook 插件到本服务
        # 事件总线；无插件即空操作。格式错误 fail-fast，不带病启动。
        await self._install_hook_plugins()
        # FEAT-07：memory provider 初始化放 serving 事件循环（lifespan 经此进入），
        # 受有限启动预算控制（默认 5s）；失败明确报错（fail-fast），不静默降级。
        provider = self._memory_provider
        initialize = getattr(provider, "initialize", None)
        if provider is not None and callable(initialize):
            try:
                await asyncio.wait_for(initialize(), timeout=5.0)
            except TimeoutError as exc:
                raise RuntimeApplicationError(
                    "memory_provider_init_timeout",
                    "personal memory provider initialization timed out",
                    status_code=503,
                ) from exc

    async def _install_hook_plugins(self) -> None:
        """启动时安装 entry_points Hook 插件（105 P1-02 / TASK-005）。

        self._event_bus 结构匹配 HookRegistryProtocol（register 方法），
        直接作为 registry 传入 loader。
        """
        from fluxion.plugins.loader import PluginLoader, discover_hook_plugins

        loader = PluginLoader(hook_registry=self._event_bus)  # type: ignore[arg-type]
        for plugin in discover_hook_plugins():
            await loader.load(plugin)

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
        # O502（TASK-008）：Runtime execution span 经 traced_scope——统一关联字段；
        # 同时绑定 execution_id ContextVar，使嵌套 Model/Tool span 自动继承。
        execution_token = bind_execution_id(request.execution_id)
        # TASK-016（ADR-A014）：所有出口走会话 finalizer；prepare 失败已在内部
        # 结算并直接抛出，不进下面的出口分支（不重复结算）。
        session = ExecutionSession(self)
        try:
            async with traced_scope(
                "runtime.execution",
                attributes={
                    "fluxion.runtime_profile_id": request.runtime_profile_id,
                },
            ) as span:
                try:
                    prepared = await session.prepare(request)
                except RuntimeApplicationError:
                    raise
                except (asyncio.CancelledError, GeneratorExit):
                    raise
                except Exception as exc:
                    # prepare 失败已在内部结算，此处只映射/日志/留痕，不重复结算。
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    failed_context = session.context
                    if failed_context is not None:
                        failed_context.emit(
                            "execution.error", {"error": _error_code(exc)}
                        )
                        await self._append_trace(
                            request,
                            failed_context,
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
                    raise RuntimeApplicationError(
                        _error_code(exc),
                        str(exc),
                        status_code=getattr(exc, "status_code", 400),
                    ) from exc
                return await self._run_prepared(
                    session, prepared, request, started, span
                )
        finally:
            reset_execution_id(execution_token)

    async def _run_prepared(
        self,
        session: ExecutionSession,
        prepared: PreparedExecution,
        request: RunRuntimeRequest,
        started: float,
        span: Any | None,
    ) -> RunRuntimeResult:
        """已准备执行的非流式主体（TASK-023）：run() 与流式 fallback 共用。

        fallback 复用同次 execution/context（不重开会话、不丢首个 context 的 trace）。
        所有出口走会话 finalizer（恰好一次终态），见 TASK-016。
        """
        context = prepared.context
        model_tools = prepared.model_tools
        allowed_model_tools = prepared.allowed_model_tools
        step_result: RuntimeStepResult | None = None
        tool_results: list[dict[str, object]] = []
        try:
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
            await session.finalize(context)
            latency_ms = _elapsed_ms(started)
            await self._append_trace(
                request,
                context,
                step_result,
                tuple(tool_results),
                latency_ms,
                None,
            )
            return _run_result(
                request,
                context,
                step_result,
                tuple(tool_results),
                latency_ms,
                self._service_instance_id,
            )
        except (asyncio.CancelledError, GeneratorExit) as exc:
            # 取消/关闭：结算为 CANCELLED（尽力），留痕后必须重新传播。
            with suppress(asyncio.CancelledError):
                await session.finalize(context, error=exc)
            await self._append_trace(
                request,
                context,
                step_result,
                tuple(tool_results),
                _elapsed_ms(started),
                "cancelled",
            )
            raise
        except Exception as exc:
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            context.emit("execution.error", {"error": _error_code(exc)})
            await session.finalize(context, error=exc)
            await self._append_trace(
                request,
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
            raise RuntimeApplicationError(
                _error_code(exc),
                str(exc),
                status_code=getattr(exc, "status_code", 400),
            ) from exc

    async def stream(self, request: RunRuntimeRequest) -> AsyncGenerator[RuntimeStreamEvent, None]:
        # review P1-4：流式主路径（Chat Channel 正式入口）此前不 bind
        # execution_id、无 runtime.execution span → 流式执行中嵌套 Model/Tool
        # span 缺 fluxion.execution_id（E-03 四字段门禁在主 Chat 路径不达标）。
        # 与 run() 对齐：ContextVar 绑定 + O502 span（mode=stream 标记）。
        execution_token = bind_execution_id(request.execution_id)
        started = perf_counter()
        try:
            async with traced_scope(
                "runtime.execution",
                attributes={
                    "fluxion.runtime_profile_id": request.runtime_profile_id,
                    "fluxion.execution.mode": "stream",
                },
            ):
                # TASK-016：所有出口走会话 finalizer。prepare 失败已在内部结算；
                # 取消/关闭显式 aclose 子生成器（使其 finally 生效）后再结算。
                # 注意：try 必须覆盖 yield started——aclose 的 GeneratorExit 恰好
                # 投递到该挂起点，此时 session 尚未创建（下分支 guard 处理）。
                session = ExecutionSession(self)
                events: AsyncGenerator[RuntimeStreamEvent, None] | None = None
                try:
                    yield RuntimeStreamEvent(
                        event="started",
                        data={
                            "request_id": request.request_id,
                            "execution_id": request.execution_id,
                            "runtime_profile_id": request.runtime_profile_id,
                            "version_selector": request.runtime_profile_version_selector,
                        },
                    )
                    events = self._stream_tokens_or_fallback(request, session)
                    async for event in events:
                        yield event
                except (asyncio.CancelledError, GeneratorExit) as exc:
                    if events is not None:
                        await events.aclose()
                    if (
                        session.context is not None
                        and session.terminal is None
                    ):
                        with suppress(asyncio.CancelledError):
                            await session.finalize(session.context, error=exc)
                        await self._append_trace(
                            request,
                            session.context,
                            None,
                            (),
                            _elapsed_ms(started),
                            "cancelled",
                        )
                    raise
        finally:
            reset_execution_id(execution_token)

    async def _stream_tokens_or_fallback(
        self, request: RunRuntimeRequest, session: ExecutionSession
    ) -> AsyncGenerator[RuntimeStreamEvent, None]:
        """无工具场景且模型支持流式时逐 token 输出，否则回退到非流式 run。

        与 run() 对齐的异常契约：流式专属路径的异常被收口为 RuntimeApplicationError
        并补 trace + error log；仅当 provider 不支持流式（返回空、不抛错）时才回退
        到非流式 run（单次模型调用）。此前任何异常都被吞成 chunks=[] 再回退 run()，
        导致流式失败被静默重试（双倍模型调用 + 错误永不暴露）。
        """
        started = perf_counter()
        context: RuntimeContext | None = None
        try:
            prepared = await session.prepare(request)
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
                # TASK-016：成功出口走会话 finalizer（恰好一次终态）。
                await session.finalize(context)
                latency_ms = _elapsed_ms(started)
                await self._append_trace(
                    request, context, step_result, tuple(tool_results), latency_ms, None
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
                await session.finalize(context)
                latency_ms = _elapsed_ms(started)
                # 此前流式成功分支只 yield completed、从不 append_trace，
                # 流式执行在 trace_store 中完全不可观测。
                await self._append_trace(request, context, None, (), latency_ms, None)
                yield RuntimeStreamEvent(
                    event="completed",
                    data=RunRuntimeResult(
                        request_id=request.request_id,
                        trace_id=context.snapshot.trace_id,
                        execution_id=context.snapshot.execution_id,
                        service_instance_id=self._service_instance_id,
                        runtime_profile_id=context.snapshot.runtime_profile_id,
                        runtime_profile_version=context.snapshot.runtime_profile_version,
                        output=output,
                        latency_ms=latency_ms,
                        model_provider_id=_streamed_provider_id(context),
                        # 该分支仅当 model_tools 为空进入（见上方），恒空是语义正确。
                        tool_results=(),
                    ).to_payload(),
                )
                return
            if _stream_completed(context):
                # TASK-023：支持流式但零 token——正常空结果，不回退、不二次请求。
                # 与上分支同语义（output=""），复用同次 execution/context 结算。
                output = ""
                await self._runtime.memory.add_message(context, "user", request.input_message)
                await self._runtime.memory.add_message(context, "assistant", output)
                await session.finalize(context)
                latency_ms = _elapsed_ms(started)
                await self._append_trace(request, context, None, (), latency_ms, None)
                yield RuntimeStreamEvent(
                    event="completed",
                    data=RunRuntimeResult(
                        request_id=request.request_id,
                        trace_id=context.snapshot.trace_id,
                        execution_id=context.snapshot.execution_id,
                        service_instance_id=self._service_instance_id,
                        runtime_profile_id=context.snapshot.runtime_profile_id,
                        runtime_profile_version=context.snapshot.runtime_profile_version,
                        output=output,
                        latency_ms=latency_ms,
                        model_provider_id=_streamed_provider_id(context),
                        tool_results=(),
                    ).to_payload(),
                )
                return
            # 明确 unsupported（model.stream_unsupported 事件）才 fallback。
            # TASK-023：复用同次 execution/context 跑非流式主体，不重开会话、
            # 不丢首个 context 的 trace；span 沿用外层流式 span（传 None 免重复）。
            reused = PreparedExecution(
                context=context,
                model_tools=[],
                allowed_model_tools=set(),
            )
            result = await self._run_prepared(session, reused, request, started, None)
            yield RuntimeStreamEvent(event="completed", data=result.to_payload())
        except RuntimeApplicationError:
            # 来自 fallback 的 _run_prepared：已完成 error log + trace + 包装，直接上抛。
            raise
        except Exception as exc:
            if context is not None:
                context.emit("execution.error", {"error": _error_code(exc)})
                await session.finalize(context, error=exc)
                await self._append_trace(
                    request,
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
            raise RuntimeApplicationError(
                _error_code(exc),
                str(exc),
                status_code=getattr(exc, "status_code", 400),
            ) from exc

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

    async def resolve_context(self, request: RunRuntimeRequest) -> dict[str, object]:
        """TASK-014：渠道 verify 专用——只构建 ExecutionSnapshot（真实 resolve 链：
        profile/agent/model/capability/credential 全量解析），不执行模型与工具。
        失败 fail-closed 抛 RuntimeApplicationError（规则 18：显式错误，不静默）。
        """
        prepared = await ExecutionSession(self).prepare(request)
        snapshot = prepared.context.snapshot
        return {
            "execution_id": snapshot.execution_id,
            "runtime_profile_id": snapshot.runtime_profile_id,
            "agent_definition_id": snapshot.agent_definition_id,
            "model_routes": len(snapshot.model_resolution.routes),
        }

    async def health(self) -> HealthResult:
        return HealthResult("ok", self._service_instance_id)

    async def ready(self, *, request_id: str = "", trace_id: str = "") -> HealthResult:
        """Readiness：Registry 读路径检测（FEAT-05）。

        预算内完成、无重试；任何连接/查询超时与故障统一 503。错误原文可能
        含 DSN/SQL——响应只给固定文案，原始错误类型进结构化日志（脱敏，
        request_id/trace_id 关联），永不回传调用方。
        """
        try:
            await asyncio.wait_for(
                self._store.get(
                    ResourceKind.RUNTIME_PROFILE,
                    "_ready",
                    tenant_id="_ready",
                    version="_ready",
                ),
                timeout=self._ready_timeout_seconds,
            )
        except (RegistryStoreError, TimeoutError) as exc:
            self._log_readiness_error(request_id, trace_id, exc)
            raise RuntimeApplicationError(
                "runtime_not_ready", "registry unavailable", status_code=503
            ) from exc
        except Exception as exc:
            self._log_readiness_error(request_id, trace_id, exc)
            raise RuntimeApplicationError(
                "runtime_not_ready", "registry unavailable", status_code=503
            ) from exc
        return HealthResult("ok", self._service_instance_id)

    def _log_readiness_error(self, request_id: str, trace_id: str, exc: BaseException) -> None:
        """readiness 失败结构化日志：只记错误类型 + 帧栈，不记异常原文。

        `traceback.format_exc()` 尾行会带异常消息（可能含 DSN/SQL），此处用
        `extract_tb` 只取帧（无局部变量值、无异常消息），满足脱敏要求。
        """
        frames = traceback.extract_tb(exc.__traceback__)
        emit_runtime_error_log(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id="system",
            execution_id="",
            runtime_profile_id="_ready",
            error_type=type(exc).__name__,
            error_code="runtime_not_ready",
            message="readiness registry check failed",
            stack="".join(traceback.format_list(frames)),
        )

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
        request: RunRuntimeRequest,
        context: RuntimeContext,
        step_result: RuntimeStepResult | None,
        tool_results: tuple[dict[str, object], ...],
        latency_ms: float,
        error: str | None,
    ) -> None:
        """Trace 持久化（TASK-015 / ADR-A014 §7 §9）：永不抛异常、有界、可观测。

        失败只记录（带 IDs 关联的 error log + 脱敏），不覆盖业务错误/终态。
        """
        events = tuple(context.trace)
        try:
            await asyncio.wait_for(
                self._trace_store.append(
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
                ),
                timeout=TRACE_WRITE_BUDGET_MS / 1000,
            )
        except TimeoutError:
            self._emit_cleanup_error(
                request, context, TimeoutError(f"trace append 超 {TRACE_WRITE_BUDGET_MS}ms 预算")
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- Trace 失败只记录
            self._emit_cleanup_error(request, context, exc)

    def _emit_cleanup_error(
        self,
        request: RunRuntimeRequest,
        context: RuntimeContext,
        exc: Exception,
    ) -> None:
        """清理失败记录（TASK-015）：失败日志脱敏且有 ID，不抛异常。"""
        context.emit("execution.cleanup_error", {"error": f"{type(exc).__name__}: {exc}"})
        emit_runtime_error_log(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            execution_id=request.execution_id,
            runtime_profile_id=request.runtime_profile_id,
            error_type="cleanup_error",
            error_code=_error_code(exc),
            message=f"cleanup failed: {exc}",
            stack=traceback.format_exc(),
        )


def _streamed_provider_id(context: RuntimeContext) -> str | None:
    """取流式实际使用的 provider（agent 在 `model.completed` 事件里记录）。

    无事件则 None（与非流式无 provider 时一致）。
    """
    provider_id: str | None = None
    for event in context.trace:
        if event.name == "model.completed":
            candidate = event.attributes.get("provider_id")
            if isinstance(candidate, str) and candidate:
                provider_id = candidate
    return provider_id


def _stream_completed(context: RuntimeContext) -> bool:
    """流式正常结束标记（TASK-023）：model.completed 且 streamed=True。

    有此标记说明 provider 支持流式（零 token 也是正常空结果），调用方不得
    fallback 二次请求模型；无标记（unsupported/无路由）才允许 fallback。
    """
    return any(
        event.name == "model.completed" and event.attributes.get("streamed") is True
        for event in context.trace
    )


def build_personal_memory_retriever(engine: object) -> PersonalMemoryRetriever:
    """FEAT-07 执行侧装配：PgVectorSemanticStore(engine) → PersonalMemoryRetriever。

    PG 通用（无 pgvector 扩展时自动降级 Python cosine，不以名字推断不可用）；engine 由
    store 持有并关闭，此处仅引用。provider 初始化在 serving 事件循环内经
    service.initialize() 完成（有限启动预算，失败明确报错）。
    """
    return PersonalMemoryRetriever(PgVectorSemanticStore(engine))


def memory_recall_timeout_from_env(default_ms: int = 1000) -> int:
    """FLUXION_MEMORY_RECALL_TIMEOUT_MS 解析（默认 1000ms；非法值回退默认）。"""
    try:
        value = int(os.environ.get("FLUXION_MEMORY_RECALL_TIMEOUT_MS", "") or default_ms)
    except ValueError:
        return default_ms
    return value if value > 0 else default_ms
