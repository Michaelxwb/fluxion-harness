from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol, cast

from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    RunnerCancelled,
)
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRateLimitedError,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelUnavailableError,
    OpenAICompatibleProvider,
    StreamingModelProvider,
)
from muad_agent_core.tools import ToolDefinition, ToolRegistry
from muad_api import AppError
from muad_api.context import current_locale, current_trace_id
from muad_api.error_codes import ErrorCode
from muad_artifact_store import SkillArtifactCache
from muad_common import SharedSettings
from muad_contracts import (
    DeliveryRouteInput,
    ResolvedAgent,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
)
from muad_platform_sdk.types import SecretValue

from ..infrastructure.audit_writer import RuntimeAuditWriter
from ..metrics import MODEL_INVOCATIONS_METRIC, TOOL_CALLS_METRIC, record_outcome
from .artifacts import ArtifactResultWriter
from .mcp_runtime_adapter import McpRuntimeAdapter, McpServerDefinition, McpToolDefinition
from .skill_tools import build_default_skill_cache, build_skill_registry
from .task_client import TaskSubmissionContext, WorkerTaskClient
from .task_tools import BackgroundTaskToolSet

CancelCheck = Callable[[], Awaitable[bool]]
MESSAGE_DELTA_EVENT = "message.delta"
TOOL_STARTED_EVENT = "tool.started"
TOOL_COMPLETED_EVENT = "tool.completed"
CANCEL_POLL_INTERVAL_SEC = 0.25
MODEL_TIMEOUT_SEC = 120.0
TOOL_RESULT_ARTIFACT_BYTES = 8 * 1024
PROVIDER_NAME = "openai-compatible"
MCP_TOOL_PREFIX = "mcp::"


@dataclass(frozen=True, slots=True)
class ExecutorEvent:
    type: str
    data: dict[str, Any]
    seq: int | None = None
    timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutorRunContext:
    tenant_id: str
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    delivery_route: DeliveryRouteInput | None = None


@dataclass(frozen=True, slots=True)
class ExecutorCredentials:
    """执行期内存凭据：MCP auth secret 不参与 repr/序列化。"""

    mcp_secrets: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    agent: ResolvedAgent
    model: ResolvedModel
    input_text: str
    is_cancel_requested: CancelCheck
    skills: tuple[ResolvedSkill, ...] = ()
    mcp_servers: tuple[ResolvedMcpServer, ...] = ()
    history: tuple[ModelMessage, ...] = ()
    credentials: ExecutorCredentials = ExecutorCredentials()
    run_context: ExecutorRunContext | None = None


class RunExecutor(Protocol):
    def run(self) -> AsyncIterator[ExecutorEvent]: ...


ExecutorFactory = Callable[[ExecutorRequest], Awaitable[RunExecutor]]


class AgentRunnerExecutor:
    def __init__(
        self,
        *,
        runner: AgentRunner,
        request: ExecutorRequest,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._runner = runner
        self._request = request
        self._close = close

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        queue: asyncio.Queue[ExecutorEvent] = asyncio.Queue()
        cancelled = asyncio.Event()
        poller = asyncio.create_task(self._poll_cancel(cancelled))
        delta_emitted = False

        async def emit(event: ExecutorEvent) -> None:
            await queue.put(event)

        async def on_delta(text: str) -> None:
            nonlocal delta_emitted
            delta_emitted = True
            await emit(ExecutorEvent(type=MESSAGE_DELTA_EVENT, data={"delta": text}))

        async def on_tool_started(call_id: str, name: str) -> None:
            await emit(
                ExecutorEvent(
                    type=TOOL_STARTED_EVENT,
                    data={"tool_call_id": call_id, "tool_name": name},
                )
            )

        async def on_tool_completed(
            call_id: str, name: str, status: str, artifact_id: str | None
        ) -> None:
            await emit(
                ExecutorEvent(
                    type=TOOL_COMPLETED_EVENT,
                    data={
                        "tool_call_id": call_id,
                        "tool_name": name,
                        "status": status,
                        "artifact_id": artifact_id,
                    },
                )
            )

        task = asyncio.create_task(
            self._execute(cancelled, on_delta, on_tool_started, on_tool_completed)
        )
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.05)
                except TimeoutError:
                    continue
                yield item
            await task
            result = task.result()
            if not delta_emitted and result is not None and result.final_text:
                # 非流式 provider：把最终回答作为单个 delta，保证渠道侧拼接可用
                yield ExecutorEvent(
                    type=MESSAGE_DELTA_EVENT, data={"delta": result.final_text}
                )
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if self._close is not None:
                await self._close()

    async def _execute(
        self,
        cancelled: asyncio.Event,
        on_delta: Callable[[str], Awaitable[None]],
        on_tool_started: Callable[[str, str], Awaitable[None]],
        on_tool_completed: Callable[[str, str, str, str | None], Awaitable[None]],
    ) -> Any:
        try:
            return await self._runner.run(
                self._build_run_request(),
                is_cancelled=cancelled.is_set,
                on_delta=on_delta,
                on_tool_started=on_tool_started,
                on_tool_completed=on_tool_completed,
            )
        except RunnerCancelled:
            return None

    def _build_run_request(self) -> AgentRunRequest:
        params = dict(self._request.model.params)
        messages = (
            tuple(self._request.history)
            if self._request.history
            else (ModelMessage(role=ModelRole.USER, content=self._request.input_text),)
        )
        return AgentRunRequest(
            model_id=self._request.model.model_id,
            instructions=self._request.agent.instructions,
            messages=messages,
            policy=AgentPolicy.from_runtime_config(self._request.agent.runtime_config),
            temperature=_float_param(params, "temperature"),
            max_tokens=_int_param(params, "max_tokens"),
            params=params,
        )

    async def _poll_cancel(self, cancelled: asyncio.Event) -> None:
        while not cancelled.is_set():
            if await self._request.is_cancel_requested():
                cancelled.set()
                return
            await asyncio.sleep(CANCEL_POLL_INTERVAL_SEC)


class AuditedModelProvider:
    """逐 attempt 模型审计：成功/重试/失败都落 model_invocation_audit。"""

    def __init__(
        self,
        inner: ModelProvider,
        writer: RuntimeAuditWriter,
        *,
        model: str,
        provider: str = PROVIDER_NAME,
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._model = model
        self._provider = provider
        self._attempt = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await self._invoke(request, None)

    async def stream(
        self,
        request: ModelRequest,
        on_delta: Callable[[str], Awaitable[None]],
    ) -> ModelResponse:
        return await self._invoke(request, on_delta)

    async def aclose(self) -> None:
        closer = getattr(self._inner, "aclose", None)
        if closer is not None:
            await closer()

    async def _invoke(
        self,
        request: ModelRequest,
        on_delta: Callable[[str], Awaitable[None]] | None,
    ) -> ModelResponse:
        started = time.monotonic()
        try:
            if on_delta is not None and hasattr(self._inner, "stream"):
                streamer = cast(StreamingModelProvider, self._inner)
                response = await streamer.stream(request, on_delta)
            else:
                response = await self._inner.complete(request)
        except ModelRateLimitedError as exc:
            await self._record(
                started, status="RETRY", retry_reason="RATE_LIMITED",
                error_code=str(ErrorCode.MODEL_UNAVAILABLE), message=str(exc),
            )
            raise
        except ModelUnavailableError as exc:
            await self._record(
                started, status="RETRY", retry_reason="UNAVAILABLE",
                error_code=str(ErrorCode.MODEL_UNAVAILABLE), message=str(exc),
            )
            raise
        except Exception:
            await self._record(
                started, status="ERROR", retry_reason=None,
                error_code=str(ErrorCode.COMMON_INTERNAL_ERROR), message="model invocation failed",
            )
            raise
        await self._record(
            started,
            status="OK",
            retry_reason=None,
            error_code=None,
            message=None,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        self._attempt = 0
        return response

    async def _record(
        self,
        started: float,
        *,
        status: str,
        retry_reason: str | None,
        error_code: str | None,
        message: str | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        attempt = self._attempt
        self._attempt += 1
        await self._writer.record_model_invocation(
            provider=self._provider,
            model=self._model,
            attempt=attempt,
            retry_reason=retry_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            status=status,
            error_code=error_code,
        )
        record_outcome(
            MODEL_INVOCATIONS_METRIC,
            status,
            {"provider": self._provider, "model": self._model},
        )


def _tool_kind(name: str) -> str:
    return "MCP" if name.startswith(MCP_TOOL_PREFIX) else "SKILL"


def _args_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolCallRecorder:
    """工具执行统一包装：审计 + 大结果 Artifact 外置。"""

    def __init__(
        self,
        *,
        context: ExecutorRunContext,
        audit_writer: RuntimeAuditWriter | None,
        artifact_writer: ArtifactResultWriter | None,
    ) -> None:
        self._context = context
        self._audit = audit_writer
        self._artifacts = artifact_writer

    async def __call__(
        self,
        definition: ToolDefinition,
        arguments: Mapping[str, Any],
        handler: Any,
    ) -> str:
        started_wall = datetime.now(UTC)
        started = time.monotonic()
        artifact_id: str | None = None
        status = "OK"
        error_code: str | None = None
        try:
            content = str(await handler(arguments))
            if self._artifacts is not None and len(content.encode("utf-8")) > TOOL_RESULT_ARTIFACT_BYTES:
                reference = await self._artifacts.persist_tool_result(
                    tenant_id=self._context.tenant_id,
                    conversation_id=self._context.conversation_id,
                    run_id=self._context.run_id,
                    task_id=None,
                    tool_call_id=definition.name,
                    tool_name=definition.name,
                    result_text=content,
                    user_id=self._context.user_id,
                )
                artifact_id = str(reference["artifact_id"])
                content = json.dumps(
                    {
                        "artifact": {
                            "artifact_id": reference["artifact_id"],
                            "size": reference["size"],
                            "checksum": reference["checksum"],
                            "preview": reference["preview"],
                        }
                    },
                    ensure_ascii=False,
                )
            return content
        except AppError as exc:
            status = "ERROR"
            error_code = str(exc.code)
            raise
        except Exception:
            status = "ERROR"
            error_code = str(ErrorCode.COMMON_INTERNAL_ERROR)
            raise
        finally:
            record_outcome(
                TOOL_CALLS_METRIC,
                status,
                {"kind": _tool_kind(definition.name), "tool": definition.name},
            )
            if self._audit is not None:
                await self._audit.record_tool_call(
                    tool_call_id=definition.name,
                    tool_name=definition.name,
                    tool_kind=_tool_kind(definition.name),
                    prepared_args_hash=_args_hash(arguments),
                    args_preview_json=_preview_args(arguments),
                    status=status,
                    start_time=started_wall,
                    end_time=datetime.now(UTC),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_code=error_code,
                    artifact_id=uuid.UUID(artifact_id) if artifact_id else None,
                )


def _preview_args(arguments: Mapping[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for key, value in arguments.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        preview[str(key)] = text[:200]
    return preview


def _wrap_registry(
    registry: ToolRegistry,
    recorder: ToolCallRecorder,
) -> ToolRegistry:
    wrapped = ToolRegistry()
    for definition in registry.list():
        handler = definition.handler
        if handler is None:
            wrapped.register(definition)
            continue
        wrapped.register(
            replace(definition, handler=partial(recorder, definition, handler=handler))
        )
    return wrapped


def _mcp_server_definition(
    server: ResolvedMcpServer,
    secrets: Mapping[str, str],
) -> McpServerDefinition:
    return McpServerDefinition(
        mcp_server_id=server.mcp_server_id,
        key=server.key,
        endpoint=server.endpoint,
        catalog_revision=server.catalog_revision,
        catalog_hash=server.catalog_hash,
        tools=[
            McpToolDefinition(
                name=str(tool.get("name") or ""),
                description=str(tool.get("description") or ""),
                input_schema=dict(tool.get("input_schema") or {"type": "object", "properties": {}}),
                effect=str(tool.get("effect") or "READ"),
            )
            for tool in server.definitions
            if tool.get("name")
        ],
        auth_secret=secrets.get(str(server.mcp_server_id)),
    )


def _task_submission_context(request: ExecutorRequest) -> TaskSubmissionContext | None:
    """从已认证 Run 上下文构造提交上下文；actor 只能来自 Run，不接受工具参数。"""
    context = request.run_context
    if context is None:
        return None
    return TaskSubmissionContext(
        tenant_id=context.tenant_id,
        actor_user_id=context.user_id,
        agent=request.agent,
        model=request.model,
        skills=tuple(request.skills),
        mcp_servers=tuple(request.mcp_servers),
        source_run_id=context.run_id,
        delivery_route=context.delivery_route,
        trace_id=current_trace_id(),
        locale=current_locale(),
    )


def build_registry(
    *,
    request: ExecutorRequest,
    cache: SkillArtifactCache,
    audit_writer: RuntimeAuditWriter | None,
    mcp_adapter: McpRuntimeAdapter | None,
    artifact_writer: ArtifactResultWriter | None = None,
    task_client: WorkerTaskClient | None = None,
) -> ToolRegistry:
    policy = AgentPolicy.from_runtime_config(request.agent.runtime_config)
    task_context = _task_submission_context(request)
    registry = build_skill_registry(
        cache=cache,
        skills=request.skills,
        policy=policy,
        task_client=task_client,
        task_context=task_context,
    )
    if task_client is not None and task_context is not None:
        # docs/04 §7.5 内置任务工具：Schedule 创建/管理与后台 Task 查询/取消。
        BackgroundTaskToolSet(
            client=task_client, context=task_context, skills=request.skills
        ).register(registry)
    if mcp_adapter is not None and request.mcp_servers and request.run_context is not None:
        mcp_adapter.register_catalog(
            registry=registry,
            tenant_id=request.run_context.tenant_id,
            run_id=request.run_context.run_id,
            servers=[
                _mcp_server_definition(server, request.credentials.mcp_secrets)
                for server in request.mcp_servers
            ],
        )
    if request.run_context is not None:
        registry = _wrap_registry(
            registry,
            ToolCallRecorder(
                context=request.run_context,
                audit_writer=audit_writer,
                artifact_writer=artifact_writer,
            ),
        )
    return registry


async def default_executor_factory(
    request: ExecutorRequest,
    *,
    skill_cache: SkillArtifactCache | None = None,
    artifact_writer: ArtifactResultWriter | None = None,
    task_client: WorkerTaskClient | None = None,
) -> RunExecutor:
    provider: ModelProvider = OpenAICompatibleProvider(
        base_url=request.model.base_url,
        model=request.model.model_id,
        api_key=await resolve_model_api_key(request.model),
        timeout_sec=MODEL_TIMEOUT_SEC,
    )
    audit_writer = _audit_writer_for(request)
    if audit_writer is not None:
        provider = AuditedModelProvider(provider, audit_writer, model=request.model.model_id)
    mcp_adapter = McpRuntimeAdapter(audit_writer=audit_writer)
    settings = SharedSettings()
    owned_task_client: WorkerTaskClient | None = None
    if task_client is None:
        owned_task_client = WorkerTaskClient(
            settings.agent_worker_url, service_token=settings.internal_service_token
        )
        task_client = owned_task_client
    registry = build_registry(
        request=request,
        cache=skill_cache or build_default_skill_cache(),
        audit_writer=audit_writer,
        mcp_adapter=mcp_adapter,
        artifact_writer=artifact_writer,
        task_client=task_client,
    )
    runner = AgentRunner(provider=provider, registry=registry, hooks=HookPipeline())

    async def close() -> None:
        await provider.aclose()  # type: ignore[attr-defined]
        await mcp_adapter.aclose()
        if owned_task_client is not None:
            await owned_task_client.aclose()

    return AgentRunnerExecutor(runner=runner, request=request, close=close)


def _audit_writer_for(request: ExecutorRequest) -> RuntimeAuditWriter | None:
    context = request.run_context
    if context is None:
        return None
    return RuntimeAuditWriter(
        tenant_id=context.tenant_id,
        run_id=context.run_id,
        task_id=None,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
    )


async def resolve_model_api_key(model: ResolvedModel) -> SecretValue:
    """密钥只从内存中的定义读取（API-07/API-09），禁止退回环境变量。"""
    if model.api_key:
        return SecretValue(value=model.api_key, version="db")
    raise AppError(ErrorCode.CREDENTIAL_MISSING)


def _float_param(params: Mapping[str, Any], key: str) -> float | None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int_param(params: Mapping[str, Any], key: str) -> int | None:
    value = params.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
