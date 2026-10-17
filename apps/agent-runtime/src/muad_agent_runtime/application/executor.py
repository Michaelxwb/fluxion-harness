from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

import httpx
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    RunnerCancelled,
)
from muad_agent_core.agent.continuation import RunnerCheckpoint, RuntimeEventPort
from muad_agent_core.agent.runner import AgentRunResult, AgentRunStatus
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import (
    DeltaCallback,
    ModelBudget,
    ModelContent,
    ModelMessage,
    ModelProvider,
    ModelRateLimitedError,
    ModelRequest,
    ModelRequestError,
    ModelResponse,
    ModelRole,
    ModelUnavailableError,
    OpenAICompatibleProvider,
    StreamingModelProvider,
    text_of,
)
from muad_agent_core.prompt import PromptSkill, bounded_catalog, prompt_skill
from muad_agent_core.tools import DEFAULT_PARALLEL_LIMIT, ToolDefinition, ToolRegistry
from muad_agent_core.tools.pipeline import (
    PreparedToolCall,
    ToolExecutionAudit,
    ToolExecutionPipeline,
    ToolPolicyDecision,
)
from muad_agent_core.tools.registry import ToolHandler
from muad_api import AppError
from muad_api.context import current_locale, current_trace_id
from muad_api.error_codes import ErrorCode
from muad_artifact_store import SkillArtifactCache
from muad_common import SharedSettings
from muad_contracts import (
    DeliveryRouteInput,
    ResolveCredentialsRequest,
    ResolvedAgent,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    RuntimeExecutionRef,
)
from muad_contracts.memory import MemoryResumeRef
from muad_contracts.model_pool import (
    ModelCallSettlement,
    ModelTokenUsage,
    RemoteCompletionProof,
    RunCallOwner,
    SelectedModelCredential,
)
from muad_contracts.platform_settings import (
    ArtifactSettings,
    CompactionSettings,
    LocaleSettings,
    MemoryPolicySettings,
    ToolResultSettings,
)
from muad_logging.redaction import redact_text
from muad_platform_sdk.types import SecretValue
from pydantic import JsonValue, ValidationError

from ..infrastructure.audit_writer import RuntimeAuditWriter
from ..infrastructure.db import get_session_factory
from ..infrastructure.gateway_delivery_client import GatewayDeliveryClient
from ..infrastructure.model_dispatch_store import MemberRef, ModelDispatchStore
from ..metrics import (
    MODEL_INVOCATIONS_METRIC,
    SKILL_CATALOG_TRUNCATED_METRIC,
    TOOL_CALLS_METRIC,
    record_counter,
    record_outcome,
)
from .async_tools.continuation_service import ExecutionIdentity
from .attachments.archive_tools import ArchiveToolSet
from .attachments.output_service import OutputArtifactWriter, OutputScope
from .attachments.tool_results import (
    ArtifactResultWriter,
    RoundCandidate,
    preview_head_tail,
    reference_payload,
    select_round_persists,
)
from .attachments.tools import AttachmentToolSet
from .context_compaction import RuntimeContextCompactor, SummaryRunner, make_summary_runner
from .context_settings import PoolExecutionPolicy
from .mcp_runtime_adapter import McpRuntimeAdapter, McpServerDefinition, McpToolDefinition
from .memory_observability import memory_audit_preview
from .memory_queries import MemoryReadScope
from .memory_runtime import RunMemory, create_run_memory
from .memory_tools import memory_write_enabled
from .model_allocator import (
    ModelAllocation,
    ModelAllocationRequest,
    ModelAllocator,
    ModelDispatchPolicy,
    ModelMemberCandidate,
)
from .model_call_trace import ModelCallTrace
from .model_capacity_service import ModelCapacityService
from .ports import CredentialsClient
from .skill_tools import TaskSubmissionProtocol, build_default_skill_cache, build_skill_registry
from .task_client import TaskSubmissionContext, WorkerTaskClient
from .task_tools import BackgroundTaskToolSet
from .time_tools import TimeToolSet, resolve_zone

logger = logging.getLogger(__name__)

CancelCheck = Callable[[], Awaitable[bool]]
MESSAGE_DELTA_EVENT = "message.delta"
MODEL_STARTED_EVENT = "model.started"
MODEL_COMPLETED_EVENT = "model.completed"
TOOL_STARTED_EVENT = "tool.started"
TOOL_COMPLETED_EVENT = "tool.completed"
# 一个 assistant 回合（含 tool_calls 与思维链）原样落库；历史重建靠它产出**合法**的消息序列
ASSISTANT_TURN_EVENT = "assistant.turn"
CANCEL_POLL_INTERVAL_SEC = 0.25
# 内容投递类工具（`externalizable_result=False`）的内联上限：**不是无限直通**。
# 超过它仍然外置，否则一个超大 SKILL.md 会直接打爆上下文；这也把「渐进式披露」从建议变成
# 硬约束（正文放 SKILL.md / 大段规范放 references）。取值与 `MAX_RESOURCE_BYTES`（单次资源
# 读取上限）对齐：工具本就被允许读这么大的正文，就不该在返回路上被截断。
MAX_INLINE_RESULT_BYTES = 256 * 1024
PROVIDER_NAME = "openai-compatible"
MCP_TOOL_PREFIX = "mcp::"
#: 资源池单次调用的**有界**重选次数：轮换竞争导致"确定未发送"后重新选择成员
POOL_RESELECTION_LIMIT = 3
#: 额度暂不可用时的重试间隔（秒）：冻结等待预算内的有界退避，不是无限自旋
POOL_CAPACITY_RETRY_INTERVAL_SEC = 0.25
#: 未知 tokenizer 的保守输入估算：每条消息的协议开销（role/字段名/分隔符）按字节计
MESSAGE_PROTOCOL_OVERHEAD_BYTES = 32



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
    submission_id: uuid.UUID | None = None
    memory_resume_ref: MemoryResumeRef | None = None


def _with_current_turn(history: tuple[ModelMessage, ...], current: ModelMessage) -> tuple[ModelMessage, ...]:
    """把**当前轮**放进要发给模型的消息序列。

    消息序列来自**会话事件回放**，而本轮的 `USER_MESSAGE` 在 Run 建立时就写进去了 ⇒ 历史末尾
    已经是这一轮的用户消息，**不能直接追加**（同一句话会出现两次）。

    - 当前消息**带内联内容**（图片内容块，设计 AD-3-B）时：替换末尾那条用户消息 —— 这正是
      "图片直发模型"能否成立的关键一步；历史里那份只有文本引用，不经替换图像块永远发不出去。
    - 当前消息是纯文本（含文档轮次）时：历史原样使用，行为与改造前逐字节一致（零回归面）。
    """
    if not history:
        return (current,)
    if isinstance(current.content, str):
        return history
    if history[-1].role == ModelRole.USER:
        return (*history[:-1], current)
    return (*history, current)


@dataclass(frozen=True, slots=True)
class ExecutorCredentials:
    """执行期内存凭据：MCP auth secret 不参与 repr/序列化。"""

    mcp_secrets: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ExecutionDefaults:
    """一个 Run 的执行期平台默认（`agent` / `memory` / `artifact` / `locale` 四组里 Run 用到的叶）。

    由 Run 创建边界从**本次取到的平台设置快照**解析一次，冻结进 `policy_json`；执行期只读这份
    冻结值——不在每轮模型调用、每次工具执行里重新取设置（NFR-PERF-01）。resume 从已冻结的
    `policy_json` 读回，配置变更只影响后续新 Run（`harness-snapshot#RULE-snapshot-001`）。

    只装 Run 真正消费的叶：`artifact` 只带 `max_archive_files`（`retention_days`/
    `cleanup_batch_size` 属 Console 清理，操作边界取，不进 Run 快照）；`locale` 只带
    `default_timezone`（`default_locale` 的 Run 侧消费方不存在）。
    """

    agent_policy: AgentPolicy = field(default_factory=AgentPolicy)
    memory_policy: MemoryPolicySettings = field(default_factory=MemoryPolicySettings)
    max_archive_files: int = field(default_factory=lambda: ArtifactSettings().max_archive_files)
    timezone: str = field(default_factory=lambda: LocaleSettings().default_timezone)


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    agent: ResolvedAgent
    model: ResolvedModel
    input_text: str
    is_cancel_requested: CancelCheck
    #: 当前消息的内容。默认用 `input_text`；带图片时由调用方给出内容块（设计 AD-3-B：
    #: 只有**当前消息**的图片内联，历史轮次只留文本引用）。
    input_content: ModelContent | None = None
    skills: tuple[ResolvedSkill, ...] = ()
    mcp_servers: tuple[ResolvedMcpServer, ...] = ()
    history: tuple[ModelMessage, ...] = ()
    credentials: ExecutorCredentials = ExecutorCredentials()
    run_context: ExecutorRunContext | None = None
    #: 本次 Run **冻结**的压缩配置（来自 `policy_json`）；None = 不压缩。
    compaction: CompactionSettings | None = None
    #: 摘要模型（`compaction.summary.model_ref` 解析后的那条模型定义 + API-09 实时凭据）。None =
    #: 摘要层本轮不跑；**没有"退回主模型"这个分支**（ADR-06）。
    summary_model: ResolvedModel | None = None
    #: 本次 Run **冻结**的执行期平台默认（来自 `policy_json`）；None = schema 默认。
    execution: ExecutionDefaults | None = None
    checkpoint: RunnerCheckpoint | None = None
    runtime_events: RuntimeEventPort | None = None
    deadline_at: datetime | None = None
    event_batch_size: int = 16
    #: 冻结的 Run 内并行上限（`policy_json.async_tools.parallel_limit`，QUALITY-03 默认 4）。
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT
    context_cursor: int | None = None
    execution_identity: ExecutionIdentity | None = None
    #: 本次 Run 冻结的模型池执行策略（`policy_json` 的 `model_pool` + `capacity` 段）。
    #: None = 旧行/直构路径：不启用资源池链路，也不补新默认（TASK-024 的读回口径）。
    pool: PoolExecutionPolicy | None = None
    #: 冻结的非密钥成员引用与权重（`policy_json.model_members`）。空元组是**合法冻结事实**
    #: （该模型当前确实没有成员）——仍然走池链路，由分配器按 `CREDENTIAL_MISSING` 明确失败。
    model_members: tuple[MemberRef, ...] | None = None
    #: API-09 受信解析客户端（密钥只进调用内存）；None = 测试/本地装配，不启用池链路。
    credentials_client: CredentialsClient | None = None


def execution_defaults_for(request: ExecutorRequest) -> ExecutionDefaults:
    """本次 Run 生效的执行期平台默认：注入的冻结值为准，缺失时回落 schema 默认。"""
    return request.execution if request.execution is not None else ExecutionDefaults()


def agent_policy_for(request: ExecutorRequest) -> AgentPolicy:
    """执行期 AgentPolicy：平台设置（冻结后注入）为基，Agent runtime_config 覆盖优先级更高。

    `execution.agent_policy` 由 Run 创建边界从平台设置快照冻结（四叶：`max_turns` /
    `max_tool_calls` / `deadline_ms` / `max_model_retries`），经 `ExecutorRequest` 显式传入；
    缺失时回落 schema 默认。
    """
    base = request.execution.agent_policy if request.execution is not None else AgentPolicy()
    return AgentPolicy.from_runtime_config(request.agent.runtime_config, base=base)


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
        model_trace: ModelCallTrace | None = None,
    ) -> None:
        self._runner = runner
        self._request = request
        self._close = close
        self._model_trace = model_trace

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        queue: asyncio.Queue[ExecutorEvent] = asyncio.Queue()
        cancelled = asyncio.Event()
        poller = asyncio.create_task(self._poll_cancel(cancelled))
        delta_emitted = False

        async def emit(event: ExecutorEvent) -> None:
            await queue.put(event)
            # Canonical writes must finish before a complete-round checkpoint is taken.
            await queue.join()

        async def on_model_started() -> None:
            data = self._model_trace.begin() if self._model_trace is not None else {}
            await emit(ExecutorEvent(type=MODEL_STARTED_EVENT, data=data))

        async def on_model_completed() -> None:
            data = self._model_trace.event_fields() if self._model_trace is not None else {}
            await emit(ExecutorEvent(type=MODEL_COMPLETED_EVENT, data=data))

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

        async def on_assistant_turn(message: ModelMessage) -> None:
            """把带 tool_calls 的 assistant 回合**原样**落事件，供后续 Run 重建合法历史。

            只有带 `tool_calls` 的回合需要落：纯文本回合已由 run 结束时的 `ASSISTANT_MESSAGE`
            承载；而思考模式**只**在带 tool_calls 时要求回传 `reasoning_content`（实测），
            所以纯文本回合的思维链不必存。
            """
            if not message.tool_calls:
                return
            await emit(
                ExecutorEvent(
                    type=ASSISTANT_TURN_EVENT,
                    data={
                        # 事件载荷只接受文本：多模态消息取其文本视图（助手回合本就是文本）
                        "text": text_of(message.content),
                        "reasoning_content": message.reasoning_content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "name": call.name,
                                "arguments": dict(call.arguments),
                            }
                            for call in message.tool_calls
                        ],
                    },
                )
            )

        async def on_tool_completed(
            call_id: str,
            name: str,
            status: str,
            artifact_id: str | None,
            result_text: str | None,
        ) -> None:
            data: dict[str, Any] = {
                "tool_call_id": call_id,
                "tool_name": name,
                "status": status,
                "artifact_id": artifact_id,
            }
            if result_text is not None:
                # 未外置的结果：正文在这里留一份**有界预览**（沿用引用预览的同一组字节上限）。
                # 不留就只有工具名进 canonical 行，下一 Run 重建历史时这条结果净丢（2026-10-06
                # review）。外置过的结果不带这个键——它有 artifact_id，重建按引用还原。
                settings = tool_result_settings(self._request)
                data["preview"] = preview_head_tail(
                    result_text,
                    head_bytes=settings.preview_head_bytes,
                    tail_bytes=settings.preview_tail_bytes,
                )
            await emit(ExecutorEvent(type=TOOL_COMPLETED_EVENT, data=data))

        task = asyncio.create_task(
            self._execute(
                cancelled,
                on_delta,
                on_tool_started,
                on_assistant_turn,
                on_tool_completed,
                on_model_started,
                on_model_completed,
            )
        )
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.05)
                except TimeoutError:
                    continue
                try:
                    yield item
                finally:
                    queue.task_done()
            await task
            result = task.result()
            if (
                not delta_emitted
                and result is not None
                and result.final_text
                and result.status == AgentRunStatus.COMPLETED
            ):
                # 非流式 provider：把最终回答作为单个 delta，保证渠道侧拼接可用
                yield ExecutorEvent(type=MESSAGE_DELTA_EVENT, data={"delta": result.final_text})
            if result is not None:
                yield ExecutorEvent(
                    type="runner.outcome",
                    data={"status": str(result.status), "final_text": result.final_text},
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
        on_assistant_turn: Callable[[ModelMessage], Awaitable[None]],
        on_tool_completed: Callable[[str, str, str, str | None, str | None], Awaitable[None]],
        on_model_started: Callable[[], Awaitable[None]],
        on_model_completed: Callable[[], Awaitable[None]],
    ) -> AgentRunResult | None:
        try:
            return await self._runner.run(
                self._build_run_request(),
                is_cancelled=cancelled.is_set,
                on_delta=on_delta,
                on_model_started=on_model_started,
                on_model_completed=on_model_completed,
                on_tool_started=on_tool_started,
                on_assistant_turn=on_assistant_turn,
                on_tool_completed=on_tool_completed,
                checkpoint=self._request.checkpoint,
                context_cursor=self._request.context_cursor,
            )
        except RunnerCancelled:
            return None

    def _build_run_request(self) -> AgentRunRequest:
        params = dict(self._request.model.params)
        current = ModelMessage(
            role=ModelRole.USER,
            content=self._request.input_content or self._request.input_text,
        )
        messages = _with_current_turn(tuple(self._request.history), current)
        return AgentRunRequest(
            model_id=self._request.model.model_id,
            instructions=self._request.agent.instructions,
            skills=self._catalog_skills(),
            messages=messages,
            policy=agent_policy_for(self._request),
            temperature=_float_param(params, "temperature"),
            max_tokens=_int_param(params, "max_tokens"),
            params=params,
            deadline_at=self._request.deadline_at,
        )

    def _catalog_skills(self) -> tuple[PromptSkill, ...]:
        """生效技能 → 目录条目，并给被上限丢掉的那些**留痕**。

        上限本身在 `DefaultPromptBuilder.build` 里收口（提示词唯一的装配点）；这里再算一次只为
        拿到 `dropped` —— agent-core 里没有 metrics 设施，而静默丢弃正是这一族里最难查的故障：
        被丢的技能模型看不见 key，对模型等于不存在。两处调的是同一个纯函数、同一组常量。
        """
        catalog = tuple(prompt_skill(skill) for skill in self._request.skills)
        _, dropped = bounded_catalog(catalog)
        if dropped:
            record_counter(SKILL_CATALOG_TRUNCATED_METRIC, len(dropped))
            logger.warning(
                "skill_catalog_truncated",
                extra={
                    "agent_key": self._request.agent.key,
                    "dropped_count": len(dropped),
                    # 丢弃规则是"取前缀"，所以第一个被丢的就是边界：报它一条即可定位，
                    # 不必把可能上百个 key 全打进日志。
                    "first_dropped_skill": dropped[0].key,
                },
            )
        return catalog

    async def _poll_cancel(self, cancelled: asyncio.Event) -> None:
        while not cancelled.is_set():
            if await self._request.is_cancel_requested():
                cancelled.set()
                return
            await asyncio.sleep(CANCEL_POLL_INTERVAL_SEC)


class ModelAttemptLedger:
    """一次 Run 的模型调用尝试序号：资源池的每次尝试与上游发送共享同一序号。

    每次上游尝试的 provider 都是新建的（按解析出的 Key/连接重建），实例内的计数器会归零；
    逐 attempt 审计要求跨尝试连续，因此序号由本账本统一发放（只增不减）。
    """

    def __init__(self) -> None:
        self._next = 0

    def take(self) -> int:
        index = self._next
        self._next += 1
        return index


class AuditedModelProvider:
    """逐 attempt 模型审计：成功/重试/失败都落 model_invocation_audit。"""

    def __init__(
        self,
        inner: ModelProvider,
        writer: RuntimeAuditWriter,
        *,
        model: str,
        provider: str = PROVIDER_NAME,
        model_trace: ModelCallTrace | None = None,
        ledger: ModelAttemptLedger | None = None,
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._model = model
        self._provider = provider
        self._attempt = 0
        self._model_trace = model_trace
        self._ledger = ledger

    def bind(self, inner: ModelProvider) -> None:
        """换绑底层 provider：资源池每次尝试按解析出的 Key/连接重建，尝试计数保持连续。"""
        self._inner = inner

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
                started,
                status="RETRY",
                retry_reason="RATE_LIMITED",
                error_code=str(ErrorCode.MODEL_UNAVAILABLE),
                message=str(exc),
            )
            raise
        except ModelUnavailableError as exc:
            await self._record(
                started,
                status="RETRY",
                retry_reason="UNAVAILABLE",
                error_code=str(ErrorCode.MODEL_UNAVAILABLE),
                message=str(exc),
            )
            raise
        except Exception:
            await self._record(
                started,
                status="ERROR",
                retry_reason=None,
                error_code=str(ErrorCode.COMMON_INTERNAL_ERROR),
                message="model invocation failed",
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
        attempt = self._ledger.take() if self._ledger is not None else self._attempt
        self._attempt += 1
        await self._writer.record_model_invocation(
            audit_id=self._model_trace.audit_id if self._model_trace is not None else None,
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


ProviderFactory = Callable[[SelectedModelCredential], ModelProvider]
SettlementOutcome = Literal["SUCCEEDED", "FAILED", "NOT_SENT", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class PoolCallScope:
    """一次 Run 的模型池调用范围：冻结策略 + 非密钥成员引用 + 调用归属（都不含密钥）。"""

    tenant_id: str
    model_id: uuid.UUID
    run_id: uuid.UUID
    lease_owner: str
    policy: PoolExecutionPolicy
    members: tuple[MemberRef, ...]
    deadline_at: datetime


@dataclass(slots=True)
class _Emitted:
    """本回合是否已经把正文外发（决定失败是"可重试"还是"必须终止"）。"""

    value: bool = False


class _RotationRace(Exception):
    """轮换竞争：预占提交后解析出的 Key 身份与预占不一致（该预占必须按"确定未发送"释放）。"""


@dataclass(frozen=True, slots=True)
class _PoolAttempt:
    """一次资源池尝试：调度结果 + 已按冻结引用核验过身份与版本的当前密钥。"""

    allocation: ModelAllocation
    credential: SelectedModelCredential


def _pool_scope_for(request: ExecutorRequest) -> PoolCallScope | None:
    """本次 Run 是否走资源池链路：冻结池策略 + 非密钥成员 + 归属 + 受信解析客户端 + 期限。

    `model_members` 为空元组是**合法冻结事实**（该模型当前确实没有成员）⇒ 仍然启用池链路，
    由分配器按 `CREDENTIAL_MISSING` 明确失败，绝不回退到过渡解析的定义内密钥。
    缺受信解析客户端（测试/本地装配）或旧行（`model_members`/`model_pool` 缺键，读回 None、
    不补新默认）时退回直连 provider——那是这些调用点既有的冻结语义。
    """
    context, identity = request.run_context, request.execution_identity
    if (
        request.pool is None
        or request.model_members is None
        or request.credentials_client is None
        or context is None
        or identity is None
        or request.deadline_at is None
    ):
        return None
    return PoolCallScope(
        tenant_id=context.tenant_id,
        model_id=request.model.id,
        run_id=context.run_id,
        lease_owner=identity.owner,
        policy=request.pool,
        members=request.model_members,
        deadline_at=request.deadline_at,
    )


def conservative_input_estimate(request: ModelRequest) -> int:
    """未知 tokenizer 的保守输入估算（design §3.7）：UTF-8 字节量 + 协议开销。

    按"1 token ≈ 1 字节"计（英文约 4 字节/token、中文约 1.5 字节/token，取字节数即高估），
    每条消息再加协议开销（role/字段名/分隔符），工具定义按同一口径计入。这是**保守估算**，
    不是精确 TPM：实际偏差由容量报告核对，真实供方额度仍需资源声明。
    """
    total = MESSAGE_PROTOCOL_OVERHEAD_BYTES * len(request.messages)
    for message in request.messages:
        total += len(text_of(message.content).encode("utf-8"))
        if message.tool_call_id is not None:
            total += len(message.tool_call_id.encode("utf-8"))
        if message.reasoning_content is not None:
            total += len(message.reasoning_content.encode("utf-8"))
        for call in message.tool_calls:
            total += len(call.name.encode("utf-8"))
            total += len(json.dumps(dict(call.arguments), ensure_ascii=False).encode("utf-8"))
    for definition in request.tools:
        total += len(definition.name.encode("utf-8"))
        total += len(definition.description.encode("utf-8"))
        total += len(
            json.dumps(dict(definition.input_schema), ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
    return total


def reserved_tokens_for(request: ModelRequest, *, policy: PoolExecutionPolicy) -> int:
    """预占 = ceil(保守输入估算 × 冻结倍率) + 本次**实际发送**的最大输出预算（design §3.7）。"""
    max_tokens = request.max_tokens
    if max_tokens is None or max_tokens < 1:
        # 启用池时输出预算必须显式填写：不读未发送的同义字段，也不猜一个上限
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    estimate = conservative_input_estimate(request)
    return max(math.ceil(estimate * policy.input_estimate_multiplier) + max_tokens, 1)


def _usage_of(response: ModelResponse) -> ModelTokenUsage | None:
    """实际 usage：两个计数都在才算已知（缺一即保守保留预占，不假定供方未计费）。"""
    if response.input_tokens is None or response.output_tokens is None:
        return None
    return ModelTokenUsage(
        input_tokens=response.input_tokens, output_tokens=response.output_tokens
    )


def _remote_proof_for(exc: BaseException, *, provider_name: str) -> RemoteCompletionProof | None:
    """失败时的远端完成证据：只有**远端应答过**的失败（429/4xx）才算已确认结束。

    传输层失败、超时与断流（agent-core 用同一异常类型表达，不区分来源）都没有"远端已停"的
    证据，一律按未知处理：UNKNOWN 保留占用，等供方确认或有证据的硬超时再释放（design §3.3）。
    """
    if isinstance(exc, ModelRateLimitedError):
        reference = "http-429"
    elif isinstance(exc, ModelRequestError):
        reference = "http-4xx"
    else:
        return None
    return RemoteCompletionProof(
        kind="PROVIDER_CONFIRMED",
        reference=f"{provider_name}:{reference}",
        completed_at=datetime.now(UTC),
    )


def _terminal_or_retryable(exc: BaseException, *, emitted: bool) -> BaseException:
    """已输出正文后的失败一律按"终止"处理（design §3.6）：另一 attempt 的回答不能拼到旧正文上。

    增量已经外发给用户了，重试会把同一段话再吐一遍；未输出正文时维持原异常，交给
    `AgentRunner` 的重试预算（429 遵守 Retry-After、5xx 有界重试）处理。
    """
    if not emitted or not isinstance(exc, (ModelRateLimitedError, ModelUnavailableError)):
        return exc
    return ModelRequestError("model call failed after partial output")


async def _aclose_provider(provider: ModelProvider) -> None:
    """关闭一次尝试的上游 provider（每次尝试独立建连接池，谁造谁收）。"""
    closer = getattr(provider, "aclose", None)
    if closer is not None:
        await closer()


class PooledModelProvider:
    """把真实模型调用接入物理资源池（design §3.2/§3.3/§3.6）。

    每次上游尝试都是一条完整链路，两个短事务之间不持锁、锁内不联网：

    1. **短事务外**读冻结成员的**非密钥**候选元信息（当前 Key 计量身份/版本/额度组/冻结权重）；
    2. `ModelAllocator` 在池锁内选择成员并预占；**提交之后**才解析密钥；
    3. 按所选 `deployment_id`+`credential_id`+`expected_revision` 走 API-09 解析当前密钥，并
       校验返回的 `key_resource_id` 与预占一致；轮换竞争（409/身份不一致）按"确定未发送"释放
       该预占并**有界重选**——绝不拿新 Key 消费旧 Key 的槽位；
    4. 发上游期间按心跳续租；失约立即停手（取消本地调用），不把停手伪装成远端已结束；
    5. 结算按证据分档：成功按**实际 usage** 释放多余预占；远端已应答的失败释放槽位但保留
       token 预占（失败不假定供方未计费）；传输层失败/断流一律 UNKNOWN 保留占用；
    6. 已输出正文后的失败按"终止"处理，另一 attempt 的回答不得拼到旧正文上；型号与上下文
       始终取自同一条冻结模型定义，不跨型号暗换。
    """

    def __init__(
        self,
        *,
        scope: PoolCallScope,
        allocator: ModelAllocator,
        capacity: ModelCapacityService,
        credentials: CredentialsClient,
        provider_factory: ProviderFactory,
        model: str,
        audit_writer: RuntimeAuditWriter | None = None,
        model_trace: ModelCallTrace | None = None,
        provider_name: str = PROVIDER_NAME,
        heartbeat_sec: float | None = None,
        reselection_limit: int = POOL_RESELECTION_LIMIT,
        wait_since: datetime | None = None,
    ) -> None:
        self._scope = scope
        self._allocator = allocator
        self._capacity = capacity
        self._credentials = credentials
        self._provider_factory = provider_factory
        self._model = model
        self._provider_name = provider_name
        self._audit_writer = audit_writer
        self._trace = model_trace
        self._heartbeat_sec = (
            heartbeat_sec
            if heartbeat_sec is not None
            else float(SharedSettings().model_call_heartbeat_sec)
        )
        self._reselection_limit = reselection_limit
        #: 本段模型等待起点：同一执行片段内的重试不刷新该预算（design §3.2/§3.7）
        self._wait_since = wait_since if wait_since is not None else datetime.now(UTC)
        self._ledger = ModelAttemptLedger()
        self._audited: AuditedModelProvider | None = None

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await self._invoke(request, None)

    async def stream(self, request: ModelRequest, on_delta: DeltaCallback) -> ModelResponse:
        return await self._invoke(request, on_delta)

    async def aclose(self) -> None:
        """无常驻连接：每次尝试的上游 provider 在 `_send` 的 finally 里关闭。"""

    async def _invoke(
        self, request: ModelRequest, on_delta: DeltaCallback | None
    ) -> ModelResponse:
        reserved_tokens = reserved_tokens_for(request, policy=self._scope.policy)
        emitted = _Emitted()
        try:
            attempt = await self._select_and_reserve(reserved_tokens)
            return await self._send(attempt, request, on_delta, emitted)
        except Exception as exc:
            mapped = _terminal_or_retryable(exc, emitted=emitted.value)
            if mapped is exc:
                raise
            raise mapped from exc

    @property
    def _dispatch_policy(self) -> ModelDispatchPolicy:
        """冻结策略 → 分配器输入（`policy_hash` 由同一 `dispatch_policy_hash` 唯一确定）。"""
        policy = self._scope.policy
        return ModelDispatchPolicy.frozen(
            dispatch_epoch_sec=policy.dispatch_epoch_sec,
            interactive_weight=policy.interactive_weight,
            background_weight=policy.background_weight,
            aging_interval_sec=policy.aging_interval_sec,
        )

    async def _select_and_reserve(self, reserved_tokens: int) -> _PoolAttempt:
        """选择 + 预占 → 按冻结引用解析 Key；轮换竞争按未发送释放并有界重选。"""
        reselects = 0
        while True:
            candidates = await self._candidates()
            allocation = await self._allocate(candidates, reserved_tokens)
            self._claim_attempt_audit_id()
            try:
                credential = await self._resolve_selected(allocation)
            except _RotationRace:
                if reselects >= self._reselection_limit:
                    raise AppError(ErrorCode.MODEL_CREDENTIAL_CHANGED) from None
                reselects += 1
                continue
            return _PoolAttempt(allocation=allocation, credential=credential)

    async def _candidates(self) -> tuple[ModelMemberCandidate, ...]:
        """短事务外读冻结成员的非密钥候选元信息（不持锁、不联网、不含密钥明文）。"""
        async with get_session_factory()() as session:
            frozen = await ModelDispatchStore(session).read_frozen_candidates(
                tenant_id=self._scope.tenant_id,
                model_id=self._scope.model_id,
                members=self._scope.members,
            )
        return tuple(ModelMemberCandidate.of(candidate) for candidate in frozen)

    async def _allocate(
        self, candidates: Sequence[ModelMemberCandidate], reserved_tokens: int
    ) -> ModelAllocation:
        """池锁内选择并预占；额度暂不可用时在冻结等待预算内退避重试（重试不刷新预算）。"""
        wait_until = min(
            self._wait_since + timedelta(seconds=self._scope.policy.model_wait_timeout_sec),
            self._scope.deadline_at,
        )
        while True:
            try:
                return await self._allocator.allocate(
                    tenant_id=self._scope.tenant_id,
                    lease_owner=self._scope.lease_owner,
                    model_id=self._scope.model_id,
                    request=ModelAllocationRequest(
                        call_attempt_id=uuid.uuid4(),
                        owner=RunCallOwner(run_id=self._scope.run_id),
                        workload="INTERACTIVE",
                        deadline_at=self._scope.deadline_at,
                        reserved_tokens=reserved_tokens,
                        wait_since=self._wait_since,
                    ),
                    candidates=candidates,
                    policy=self._dispatch_policy,
                )
            except AppError as exc:
                if (
                    exc.code != str(ErrorCode.MODEL_CAPACITY_UNAVAILABLE)
                    or datetime.now(UTC) >= wait_until
                ):
                    raise
                await asyncio.sleep(POOL_CAPACITY_RETRY_INTERVAL_SEC)

    async def _resolve_selected(self, allocation: ModelAllocation) -> SelectedModelCredential:
        """按冻结引用解析所选成员的当前密钥，并校验身份与预占一致（design §3.3 调用链）。"""
        member = allocation.member
        payload = ResolveCredentialsRequest(
            execution_ref=RuntimeExecutionRef(type="RUN", id=self._scope.run_id),
            model_id=self._scope.model_id,
            deployment_id=member.deployment_id,
            credential_id=member.credential_id,
            expected_revision=member.credential_revision,
        )
        try:
            data = await self._credentials.resolve_credentials(
                tenant_id=self._scope.tenant_id,
                payload=payload.model_dump(mode="json"),
                trace_id=current_trace_id() or "",
            )
            credential = SelectedModelCredential.model_validate(data.get("model"))
        except AppError as exc:
            if exc.code != str(ErrorCode.MODEL_CREDENTIAL_CHANGED):
                # 预占后、发上游前的任何解析失败都**确定未发送**：释放预占，不保留占用
                await self._release_unsent(allocation, reason="RESOLVE_FAILED", error_code=exc.code)
                raise
            await self._release_unsent(
                allocation, reason="CREDENTIAL_CHANGED", error_code=exc.code
            )
            raise _RotationRace from exc
        except (ValidationError, AttributeError) as exc:
            await self._release_unsent(
                allocation, reason="RESOLVE_INVALID", error_code=str(ErrorCode.COMMON_INTERNAL_ERROR)
            )
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        if (
            credential.deployment_id != member.deployment_id
            or credential.credential_id != member.credential_id
            or credential.key_resource_id != member.key_resource_id
        ):
            await self._release_unsent(
                allocation, reason="KEY_IDENTITY_MISMATCH", error_code=str(ErrorCode.MODEL_CREDENTIAL_CHANGED)
            )
            raise _RotationRace
        return credential

    def _claim_attempt_audit_id(self) -> None:
        """为本次尝试认领审计行身份（第一次尝试复用回合 id，重选各铸新 id）。"""
        if self._trace is not None:
            self._trace.begin_attempt()

    async def _release_unsent(
        self, allocation: ModelAllocation, *, reason: str, error_code: str
    ) -> None:
        """确定未发送：释放该预占（回收在飞与窗口计数），并留一条 attempt 审计行。"""
        await self._capacity.release_unsent(
            tenant_id=self._scope.tenant_id,
            call_attempt_id=allocation.call_attempt_id,
            epoch=allocation.lease_epoch,
            lease_owner=allocation.lease_owner,
        )
        await self._record_attempt(status="RETRY", retry_reason=reason, error_code=error_code)

    async def _record_attempt(
        self, *, status: str, retry_reason: str, error_code: str
    ) -> None:
        """逐 attempt 审计：资源池的每次尝试（含轮换释放）都有自己的审计行与尝试序号。"""
        if self._audit_writer is None:
            return
        await self._audit_writer.record_model_invocation(
            provider=self._provider_name,
            model=self._model,
            attempt=self._ledger.take(),
            retry_reason=retry_reason,
            input_tokens=None,
            output_tokens=None,
            latency_ms=None,
            status=status,
            error_code=error_code,
            audit_id=self._trace.audit_id if self._trace is not None else None,
        )

    def _audited_for(self, inner: ModelProvider) -> ModelProvider:
        """把每次尝试的上游 provider 接到同一个审计包装上（尝试序号跨尝试连续）。"""
        if self._audit_writer is None:
            return inner
        if self._audited is None:
            self._audited = AuditedModelProvider(
                inner,
                self._audit_writer,
                model=self._model,
                provider=self._provider_name,
                model_trace=self._trace,
                ledger=self._ledger,
            )
        else:
            self._audited.bind(inner)
        return self._audited

    async def _send(
        self,
        attempt: _PoolAttempt,
        request: ModelRequest,
        on_delta: DeltaCallback | None,
        emitted: _Emitted,
    ) -> ModelResponse:
        """发上游（带续租心跳）并按证据结算；上游 provider 每次尝试独立创建并关闭。"""
        inner = self._provider_factory(attempt.credential)
        provider = self._audited_for(inner)
        lost, stop = asyncio.Event(), asyncio.Event()
        renewer = asyncio.create_task(
            self._renew_loop(attempt.allocation, stop=stop, lost=lost)
        )
        try:
            response = await self._call_until_lost(provider, request, on_delta, emitted, lost)
        except asyncio.CancelledError:
            await self._mark_unknown_quietly(attempt.allocation)
            raise
        except Exception as exc:
            await self._settle_failure(attempt.allocation, exc)
            raise
        finally:
            stop.set()
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer
            await _aclose_provider(inner)
        await self._settle_response(attempt.allocation, response)
        return response

    async def _call_until_lost(
        self,
        provider: ModelProvider,
        request: ModelRequest,
        on_delta: DeltaCallback | None,
        emitted: _Emitted,
        lost: asyncio.Event,
    ) -> ModelResponse:
        """发上游并监听失约：续租失败立即停手，本地调用取消（远端未知仍保留 UNKNOWN 占额）。"""
        call = asyncio.create_task(self._call(provider, request, on_delta, emitted))
        lost_wait = asyncio.create_task(lost.wait())
        done, _ = await asyncio.wait({call, lost_wait}, return_when=asyncio.FIRST_COMPLETED)
        if call in done:
            lost_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lost_wait
            return await call
        call.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await call
        # 被取消的调用不会走审计包装的错误分支：停手本身必须留一条 attempt 审计
        await self._record_attempt(
            status="ERROR" if emitted.value else "RETRY",
            retry_reason="LEASE_LOST",
            error_code=str(ErrorCode.MODEL_CALL_LEASE_LOST),
        )
        raise ModelUnavailableError("model call lease lost")

    async def _call(
        self,
        provider: ModelProvider,
        request: ModelRequest,
        on_delta: DeltaCallback | None,
        emitted: _Emitted,
    ) -> ModelResponse:
        streamer = getattr(provider, "stream", None)
        if on_delta is None or streamer is None:
            return await provider.complete(request)

        async def tracked(text: str) -> None:
            emitted.value = True
            await on_delta(text)

        return await cast(StreamingModelProvider, provider).stream(request, tracked)

    async def _renew_loop(
        self, allocation: ModelAllocation, *, stop: asyncio.Event, lost: asyncio.Event
    ) -> None:
        """调用期间续租心跳：失约（非 ACTIVE/已过期/epoch/owner 不符）即置 lost，执行方停手。

        瞬时故障（数据库抖动）不退出：下一拍照续（与 Run 租约心跳同一口径）。
        """
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_sec)
                return
            except TimeoutError:
                pass
            try:
                await self._capacity.renew(
                    tenant_id=self._scope.tenant_id,
                    call_attempt_id=allocation.call_attempt_id,
                    epoch=allocation.lease_epoch,
                    lease_owner=allocation.lease_owner,
                    deadline_at=self._scope.deadline_at,
                )
            except AppError as exc:
                logger.warning(
                    "model_call_lease_lost",
                    extra={"run_id": str(self._scope.run_id), "code": exc.code},
                )
                record_outcome(
                    MODEL_INVOCATIONS_METRIC,
                    "LEASE_LOST",
                    {"provider": self._provider_name, "model": self._model},
                )
                lost.set()
                return
            except Exception as exc:  # noqa: BLE001 —— 心跳是后台任务，瞬时故障不得带走调用
                logger.warning(
                    "model_call_renew_failed",
                    extra={"run_id": str(self._scope.run_id), "error": type(exc).__name__},
                )

    async def _settle_response(
        self, allocation: ModelAllocation, response: ModelResponse
    ) -> None:
        """按响应结算：有收尾信号才算远端确认结束；缺 usage 一律保守保留预占。"""
        if not response.finish_reason:
            # 断流：连接结束但没有收尾信号 ⇒ 无法确认远端结束，保留 UNKNOWN 占用
            logger.warning(
                "model_stream_truncated",
                extra={"run_id": str(self._scope.run_id), "model": self._model},
            )
            record_outcome(
                MODEL_INVOCATIONS_METRIC,
                "UNKNOWN",
                {"provider": self._provider_name, "model": self._model},
            )
            await self._settle(allocation, outcome="UNKNOWN", usage=None, proof=None)
            return
        await self._settle(
            allocation,
            outcome="SUCCEEDED",
            usage=_usage_of(response),
            proof=RemoteCompletionProof(
                kind="PROVIDER_CONFIRMED",
                reference=f"{self._provider_name}:{response.finish_reason}",
                completed_at=datetime.now(UTC),
            ),
        )

    async def _settle_failure(
        self, allocation: ModelAllocation, exc: BaseException
    ) -> None:
        """失败结算：远端已应答的失败释放槽位、保留 token 预占；其余保留 UNKNOWN 占用。"""
        proof = _remote_proof_for(exc, provider_name=self._provider_name)
        await self._settle(
            allocation,
            outcome="FAILED" if proof is not None else "UNKNOWN",
            usage=None,
            proof=proof,
        )

    async def _mark_unknown_quietly(self, allocation: ModelAllocation) -> None:
        """取消/异常退出：无法确认远端结束 ⇒ UNKNOWN 保留占用（绝不当作"未发送"释放）。"""
        try:
            await self._capacity.mark_unknown(
                tenant_id=self._scope.tenant_id,
                call_attempt_id=allocation.call_attempt_id,
                epoch=allocation.lease_epoch,
                lease_owner=allocation.lease_owner,
            )
        except AppError as exc:
            logger.warning(
                "model_call_unknown_mark_failed",
                extra={"run_id": str(self._scope.run_id), "code": exc.code},
            )

    async def _settle(
        self,
        allocation: ModelAllocation,
        *,
        outcome: SettlementOutcome,
        usage: ModelTokenUsage | None,
        proof: RemoteCompletionProof | None,
    ) -> None:
        """结算一次尝试；结算失败不得掩盖调用结果，但必须留痕（warning + 指标）。"""
        try:
            await self._capacity.settle(
                tenant_id=self._scope.tenant_id,
                call_attempt_id=allocation.call_attempt_id,
                settlement=ModelCallSettlement(
                    epoch=allocation.lease_epoch,
                    lease_owner=allocation.lease_owner,
                    outcome=outcome,
                    usage=usage,
                    remote_completion=proof,
                ),
            )
        except AppError as exc:
            logger.warning(
                "model_call_settle_failed",
                extra={"run_id": str(self._scope.run_id), "code": exc.code},
            )
            record_outcome(
                MODEL_INVOCATIONS_METRIC,
                "SETTLE_FAILED",
                {"provider": self._provider_name, "model": self._model},
            )


def _tool_kind(name: str) -> str:
    return "MCP" if name.startswith(MCP_TOOL_PREFIX) else "SKILL"

def _args_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tool_error_code(exc: BaseException) -> str:
    """工具异常 → `tool_call_audit.error_code`。

    不只是 `AppError`：各工具自己的错误类型（`AttachmentToolError`/`ArchiveToolError`/
    `SkillToolError`/`MemoryToolError`…）**一律带 `.code`**，此前却统统落进兜底、被记成
    `COMMON_INTERNAL_ERROR` —— 于是"附件不存在""ZIP 超限"和"运行时真炸了"在审计里长得
    一模一样，按错误码做的统计也就没有意义（2026-10-03 review）。

    无 `.code` 的异常才是真的意外，保持兜底码。
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


@dataclass(frozen=True, slots=True)
class _RoundCall:
    """本回合一次调用的缓冲：判定要用的工具定义 + 审计要用的字段。"""

    definition: ToolDefinition
    status: str
    error_code: str | None
    started_wall: datetime
    latency_ms: int
    args_hash: str
    args_preview: dict[str, Any]


class ToolCallRecorder:
    """工具执行统一包装：**整轮批次预算** + 审计 + 把产物 id 交给上层报事件。

    判定单元是**回合**而不是单条调用（design ADR-04）：单条超阈值要落盘，多条**合计**超整轮预算
    也要落盘 —— 后者只有把本轮全部结果放在一起看才算得出。所以 `__call__` 不再逐条外置，改为把
    结果与审计字段缓存进本回合缓冲；回合末由 `finish_round` 一次性判定、批量落盘（失败整批回滚）、
    把被外置的结果换成引用，再逐条写审计行。产物 id 因此赶得上 `tool.completed`（它进了
    canonical 行，历史重建靠它把产物找回来）。
    """

    def __init__(
        self,
        *,
        context: ExecutorRunContext,
        audit_writer: RuntimeAuditWriter | None,
        artifact_writer: ArtifactResultWriter | None,
        settings: ToolResultSettings | None = None,
        credential_canaries: tuple[str, ...] = (),
    ) -> None:
        self._context = context
        self._audit = audit_writer
        self._artifacts = artifact_writer
        self._settings = settings or ToolResultSettings()
        self._round: dict[str, _RoundCall] = {}
        self._credential_canaries = credential_canaries
        self._previews: dict[str, dict[str, JsonValue]] = {}

    def prepare(self, call: PreparedToolCall) -> None:
        self._previews[call.call_id] = _preview_args(call.arguments, tool_name=call.tool_name,
            args_hash=call.args_hash, status="PREPARED", canaries=self._credential_canaries)

    async def __call__(
        self,
        definition: ToolDefinition,
        arguments: Mapping[str, Any],
        handler: ToolHandler,
        *,
        call_id: str,
    ) -> str:
        """执行一次工具并**只做缓冲**：落盘与否要等本轮结果到齐（ADR-04）。"""
        registry = ToolRegistry()
        registry.register(replace(definition, handler=handler))
        pipeline = ToolExecutionPipeline(registry=registry, audit=self)
        prepared = pipeline.prepare(call_id=call_id, tool_name=definition.name, arguments=arguments)
        return (await pipeline.execute(prepared)).content

    async def record(self, event: ToolExecutionAudit) -> None:
        call = event.prepared
        status = (
            "OK" if event.status == "SUCCEEDED" else "DENY" if event.status == "POLICY_DENIED" else "ERROR"
        )
        preview = self._previews.pop(call.call_id)
        if call.tool_name == "remember":
            preview["status"] = status
        record_outcome(
            TOOL_CALLS_METRIC, status, {"kind": _tool_kind(call.tool_name), "tool": call.tool_name}
        )
        if event.definition is None or status == "DENY":
            if self._audit is not None:
                await self._audit.record_tool_call(
                    tool_call_id=call.call_id,
                    tool_name=call.tool_name,
                    tool_kind=_tool_kind(call.tool_name),
                    prepared_args_hash=call.args_hash,
                    args_preview_json=preview,
                    status=status,
                    start_time=event.started_at,
                    end_time=datetime.now(UTC),
                    latency_ms=event.latency_ms,
                    error_code=event.error_code,
                )
            return
        self._round[call.call_id] = _RoundCall(
            definition=event.definition,
            status=status,
            error_code=event.error_code,
            started_wall=event.started_at,
            latency_ms=event.latency_ms,
            args_hash=call.args_hash,
            args_preview=preview,
        )

    async def finish_round(self, results: Sequence[ModelMessage]) -> Sequence[ModelMessage]:
        """整轮判定 → 批量落盘 → 替换内容 → 逐条写审计行。返回与入参**等长同序**的消息序列。"""
        if not results:
            return tuple(results)
        call_ids = [message.tool_call_id or "" for message in results]
        contents = [text_of(message.content) for message in results]
        selected = self._select(call_ids, contents)
        references = await self._persist(call_ids, contents, selected)
        await self._flush_audit(call_ids, references)
        return tuple(
            replace(message, content=reference_payload(references[call_id]))
            if call_id in references
            else message
            for message, call_id in zip(results, call_ids, strict=True)
        )

    def _select(self, call_ids: list[str], contents: list[str]) -> set[int]:
        """本回合要外置的下标：内容投递类按内联上限**单条**判定且不进整轮预算，其余走整轮选取。"""
        forced: set[int] = set()
        candidates: list[RoundCandidate] = []
        for index, (call_id, content) in enumerate(zip(call_ids, contents, strict=True)):
            record = self._round.get(call_id)
            size = len(content.encode("utf-8"))
            if record is not None and not record.definition.externalizable_result:
                # 内容投递类工具（`load_skill` / `read_skill_resource`）的返回**就是要给模型读的
                # 正文**，按 8KB 截成预览等于把工具废掉（2026-10-01 事故：8320 字节的
                # `load_skill` 返回值被外置，模型只拿到 400 字符预览，而且**没有任何报错**，
                # 两侧行为差异直到与另一产品对比才暴露）。
                if size > MAX_INLINE_RESULT_BYTES:
                    forced.add(index)
                continue
            candidates.append(RoundCandidate(call_id, size))
        chosen = set(
            select_round_persists(
                candidates,
                persist_threshold_bytes=self._settings.persist_threshold_bytes,
                round_budget_bytes=self._settings.round_budget_bytes,
            )
        )
        forced.update(index for index, call_id in enumerate(call_ids) if call_id in chosen)
        return forced

    async def _persist(
        self, call_ids: list[str], contents: list[str], selected: set[int]
    ) -> dict[str, dict[str, Any]]:
        """批量落盘。失败**整批回滚**并退化成"这批不外置"（外置是尽力而为，不得让 Run 失败）。"""
        if not selected or self._artifacts is None:
            return {}
        payload = [
            (call_ids[index], self._tool_name(call_ids[index]), contents[index]) for index in sorted(selected)
        ]
        try:
            async with get_session_factory()() as session:
                return await self._artifacts.persist_round_results_with_session(
                    session,
                    tenant_id=self._context.tenant_id,
                    conversation_id=self._context.conversation_id,
                    run_id=self._context.run_id,
                    task_id=None,
                    results=payload,
                    preview_head_bytes=self._settings.preview_head_bytes,
                    preview_tail_bytes=self._settings.preview_tail_bytes,
                )
        except Exception as exc:  # noqa: BLE001 —— 退化后正文照旧内联，Run 不受影响
            logger.warning(
                "tool_round_persist_failed",
                extra={"run_id": str(self._context.run_id), "error": str(exc)},
            )
            return {}

    def _tool_name(self, call_id: str) -> str:
        record = self._round.get(call_id)
        return record.definition.name if record is not None else ""

    async def _flush_audit(self, call_ids: list[str], references: Mapping[str, Mapping[str, Any]]) -> None:
        """逐条写审计行（带最终 `artifact_id`）并清空本回合缓冲。"""
        for call_id in call_ids:
            record = self._round.pop(call_id, None)
            if record is None or self._audit is None:
                continue
            reference = references.get(call_id)
            await self._audit.record_tool_call(
                tool_call_id=call_id,
                tool_name=record.definition.name,
                tool_kind=_tool_kind(record.definition.name),
                prepared_args_hash=record.args_hash,
                args_preview_json=record.args_preview,
                status=record.status,
                start_time=record.started_wall,
                end_time=datetime.now(UTC),
                latency_ms=record.latency_ms,
                error_code=record.error_code,
                artifact_id=uuid.UUID(str(reference["artifact_id"])) if reference else None,
            )


def _preview_args(
    arguments: Mapping[str, object], *, tool_name: str = "", args_hash: str = "", status: str = "",
    canaries: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    """工具的入参预览（落 `runtime.tool_call_audit.args_preview_json`）。

    **先截断、再过 `redact_text`**：预览里装的是**模型自己写的内容**——
    `write_artifact(content=...)`、`create_archive(files=[{content: ...}])` 都会把正文开头塞进来。
    模型完全可能在文件里写一段带 `api_key=…`/`token=…` 的配置，而那 200 字符会**落进审计表**。
    `RULE-secret-001` 要求密钥不得进入日志与审计，所以这里复用**同一套**脱敏策略
    （`muad_logging.redaction`）——不是另写一份扫密钥逻辑（那才会两处漂移）。

    顺序是「先截断后脱敏」：先脱敏意味着对一个可能几 MB 的值跑正则，代价与收益不成比例；
    200 字符窗口内**完整出现**的键值对已能被覆盖，而这正是实际的泄漏形态。
    """
    if tool_name == "remember":
        return memory_audit_preview(arguments, args_hash=args_hash, status=status, canaries=canaries)
    preview: dict[str, JsonValue] = {}
    for key, value in arguments.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        preview[str(key)] = redact_text(text[:200])
    return preview


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
    task_submitter: TaskSubmissionProtocol | None = None,
    delivery_client: GatewayDeliveryClient | None = None,
    recorder: ToolCallRecorder | None = None,
    memory: RunMemory | None = None,
) -> ToolRegistry:
    policy = agent_policy_for(request)
    execution = execution_defaults_for(request)
    task_context = _task_submission_context(request)
    registry = build_skill_registry(
        cache=cache,
        skills=request.skills,
        policy=policy,
        task_client=task_submitter,
        task_context=task_context,
    )
    if task_client is not None and task_context is not None:
        # docs/04 §7.5 内置任务工具：Schedule 创建/管理与后台 Task 查询/取消。
        BackgroundTaskToolSet(client=task_client, context=task_context, skills=request.skills).register(
            registry
        )
    # 内置时间工具：模型不知道"现在几点"，而 create_schedule 与相对时间（"明天早上 9 点"）都依赖它。
    # 时区取**本次 Run 冻结的**平台默认 IANA 名，与调度侧口径一致（harness-time#RULE-time-001）。
    TimeToolSet(zone=resolve_zone(execution.timezone)).register(registry)
    if request.run_context is not None:
        # 附件读取工具：类型无关、入口无关（AD-4-B）。租户与**归属用户**都从 Run 上下文取，
        # **不进工具 schema**（归属一旦成为模型可填的参数，越权就只剩一层校验，NFR-SEC-01）。
        AttachmentToolSet(
            session_factory=get_session_factory,
            artifact_root=SharedSettings().artifact_root,
            tenant_id=request.run_context.tenant_id,
            run_id=request.run_context.run_id,
            conversation_id=request.run_context.conversation_id,
            user_id=request.run_context.user_id,
            # TASK-005 起「写」不再依赖交付路由；`has_delivery_route` 只喂返回文案里的提示。
            has_delivery_route=request.run_context.delivery_route is not None,
            # 交付动作才需要路由与客户端（TASK-006）：没有路由时 `deliver_artifact` 明确报错
            delivery_route=request.run_context.delivery_route,
            delivery_client=delivery_client,
        ).register(registry)
        ArchiveToolSet(
            # 回执裁剪与外置判定必须用**同一个生效阈值**（否则调低阈值后回执自己会被外置，
            # 模型看不到 deliver_artifact 指引）
            receipt_limit_bytes=tool_result_settings(request).persist_threshold_bytes,
            max_archive_files=execution.max_archive_files,
            writer=OutputArtifactWriter(
                artifact_root=SharedSettings().artifact_root,
                session_factory=get_session_factory,
                scope=OutputScope(
                    request.run_context.tenant_id,
                    request.run_context.run_id,
                    request.run_context.conversation_id,
                ),
            ),
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
    memory = memory if memory is not None else _memory_runtime_for(request)
    if memory is not None:
        memory.tools.register(registry)
    return registry


def _memory_runtime_for(request: ExecutorRequest) -> RunMemory | None:
    context = request.run_context
    if context is None:
        return None
    policy = execution_defaults_for(request).memory_policy
    policy = replace(policy, write_enabled=memory_write_enabled(
        request.agent.runtime_config, default=policy.write_enabled))
    credentials = (request.model.api_key, *request.credentials.mcp_secrets.values())
    canaries = tuple(value for value in credentials if value)
    return create_run_memory(get_session_factory(),
        scope=MemoryReadScope(tenant_id=context.tenant_id, user_id=context.user_id), run_id=context.run_id,
        settings=policy, query=request.input_text, identity=request.execution_identity,
        submission_id=context.submission_id, credential_canaries=canaries,
        resume_ref=context.memory_resume_ref)


def tool_result_settings(request: ExecutorRequest) -> ToolResultSettings:
    """整轮批次预算与预览阈值：取自**冻结**配置，没有冻结配置时回落 schema 默认值。"""
    return request.compaction.tool_result if request.compaction is not None else ToolResultSettings()


async def default_executor_factory(
    request: ExecutorRequest,
    *,
    skill_cache: SkillArtifactCache | None = None,
    artifact_writer: ArtifactResultWriter | None = None,
    task_client: WorkerTaskClient | None = None,
    delivery_client: GatewayDeliveryClient | None = None,
    model_transport: httpx.AsyncBaseTransport | None = None,
) -> RunExecutor:
    # 单次模型请求 I/O 超时由预算层级派生（单次请求预算被夹到剩余总预算），不再独立取默认。
    # 基值取冻结的 platform agent 策略（与 `AgentPolicy` 的 deadline 同源）；I/O 超时是**单次请求
    # 上限**，不含 Agent 的 runtime_config 覆盖（覆盖由 `AgentRunner` 在重试/截止判定里生效）。
    frozen = request.execution.agent_policy if request.execution is not None else AgentPolicy()
    budget = ModelBudget(deadline_ms=frozen.deadline_ms, max_model_retries=frozen.max_model_retries)
    audit_writer = _audit_writer_for(request)
    model_trace = ModelCallTrace() if audit_writer is not None else None
    scope = _pool_scope_for(request)
    if scope is not None:
        # 启用池的 Run：预占→按冻结引用解析 Key→发上游→续租→结算全在 `PooledModelProvider`
        provider: ModelProvider = _pooled_provider_for(
            request,
            scope=scope,
            audit_writer=audit_writer,
            model_trace=model_trace,
            timeout_sec=budget.request_timeout_sec(),
            transport=model_transport,
        )
    else:
        provider = await _model_provider(
            request.model, timeout_sec=budget.request_timeout_sec(), transport=model_transport
        )
        if audit_writer is not None:
            provider = AuditedModelProvider(
                provider, audit_writer, model=request.model.model_id, model_trace=model_trace
            )
    # 摘要模型是**另一条模型定义**（ADR-06）：endpoint / 模型名 / 凭据都取自它自己那一份，凭据
    # 由 API-09 在 Run 边界取到、随 `ExecutorRequest` 进内存。没有它就不装摘要层，绝不退回主模型。
    owned_summary_provider: ModelProvider | None = None
    summary_provider: ModelProvider | None = None
    if request.summary_model is not None:
        owned_summary_provider = await _model_provider(
            request.summary_model,
            timeout_sec=budget.request_timeout_sec(),
            transport=model_transport,
        )
        summary_provider = owned_summary_provider
        if audit_writer is not None:
            summary_provider = AuditedModelProvider(
                summary_provider, audit_writer, model=request.summary_model.model_id
            )
    mcp_adapter = McpRuntimeAdapter(audit_writer=audit_writer)
    settings = SharedSettings()
    # 交付客户端：**只有本次 Run 有交付路由时才建**（没路由的 Run 谈不上交付）。
    # 与 task_client 同一条 owned/close 规矩：谁造谁收，避免每次 Run 漏一个 httpx 连接池。
    owned_delivery_client: GatewayDeliveryClient | None = None
    if delivery_client is None and request.run_context is not None:
        if request.run_context.delivery_route is not None:
            owned_delivery_client = GatewayDeliveryClient(
                settings.im_gateway_url, service_token=settings.internal_service_token
            )
            delivery_client = owned_delivery_client
    owned_task_client: WorkerTaskClient | None = None
    if task_client is None:
        owned_task_client = WorkerTaskClient(
            settings.agent_worker_url, service_token=settings.internal_service_token
        )
        task_client = owned_task_client
    # 工具结果的**整轮批次预算**要同时挂在两个地方：注册表里逐条执行时缓冲结果，回合末由
    # `AgentRunner` 调同一个对象收口（design ADR-04）——所以这里只建一份，两处共用。
    recorder: ToolCallRecorder | None = None
    if request.run_context is not None:
        recorder = ToolCallRecorder(
            context=request.run_context,
            audit_writer=audit_writer,
            artifact_writer=artifact_writer,
            settings=tool_result_settings(request),
            credential_canaries=tuple(value for value in
                (request.model.api_key, *request.credentials.mcp_secrets.values()) if value),
        )
    memory = _memory_runtime_for(request)
    registry = build_registry(
        request=request,
        cache=skill_cache or build_default_skill_cache(),
        audit_writer=audit_writer,
        mcp_adapter=mcp_adapter,
        artifact_writer=artifact_writer,
        task_client=task_client,
        task_submitter=_durable_submitter(settings, request),
        delivery_client=delivery_client,
        recorder=recorder,
        memory=memory,
    )
    hooks = HookPipeline()
    runner = AgentRunner(
        provider=provider,
        registry=registry,
        hooks=hooks,
        tool_pipeline=ToolExecutionPipeline(
            registry=registry, audit=recorder, policy=_frozen_tool_policy(request), hooks=hooks
        ),
        context_compactor=_context_compactor(request, summary_provider, artifact_root=settings.artifact_root),
        tool_round_results=recorder,
        runtime_events=request.runtime_events,
        context_loader=memory.loader if memory is not None else None,
        tool_suspension=memory.confirmation if memory is not None else None,
        event_batch_size=request.event_batch_size,
        parallel_limit=request.parallel_limit,
    )

    async def close() -> None:
        await provider.aclose()  # type: ignore[attr-defined]
        if owned_summary_provider is not None:
            await owned_summary_provider.aclose()  # type: ignore[attr-defined]
        await mcp_adapter.aclose()
        if owned_task_client is not None:
            await owned_task_client.aclose()
        if owned_delivery_client is not None:
            await owned_delivery_client.aclose()

    return AgentRunnerExecutor(runner=runner, request=request, close=close, model_trace=model_trace)


def _frozen_tool_policy(request: ExecutorRequest) -> Callable[[PreparedToolCall], ToolPolicyDecision]:
    skills = {skill.key: skill for skill in request.skills}

    def authorize(call: PreparedToolCall) -> ToolPolicyDecision:
        if call.tool_name in ("load_skill", "read_skill_resource", "execute_skill", "run_skill_script"):
            key = call.arguments.get("skill_key")
            skill = skills.get(key) if isinstance(key, str) else None
            if skill is None:
                return ToolPolicyDecision(False, "Skill is not effective for this Run", "FORBIDDEN")
            if (
                call.tool_name == "execute_skill"
                and skill.execution_mode.value == "SYNC"
                and "completion_mode" in call.arguments
            ):
                return ToolPolicyDecision(
                    False, "SYNC Skill does not accept completion_mode", "TOOL_COMPLETION_MODE_INVALID"
                )
        return ToolPolicyDecision(True)

    return authorize


def _durable_submitter(settings: SharedSettings, request: ExecutorRequest) -> TaskSubmissionProtocol | None:
    if request.run_context is None or not any(
        skill.execution_mode.value == "ASYNC" for skill in request.skills
    ):
        return None
    from .async_tools.operations import DurableTaskSubmitter

    # Client belongs to the process lifespan, so it survives suspended Run segments.
    from .async_tools.runtime import get_control_dispatcher

    dispatcher = get_control_dispatcher()
    return DurableTaskSubmitter(get_session_factory(), dispatcher)


def _context_compactor(
    request: ExecutorRequest,
    summary_provider: ModelProvider | None,
    *,
    artifact_root: str,
) -> RuntimeContextCompactor | None:
    """请求缝的压缩器：只在本次 Run 有冻结配置与 Run 上下文时才装。"""
    context = request.run_context
    if context is None or request.compaction is None:
        return None
    return RuntimeContextCompactor(
        settings=request.compaction,
        tenant_id=context.tenant_id,
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        artifact_root=artifact_root,
        summary_runner=_summary_runner(summary_provider, request),
    )


def _summary_runner(provider: ModelProvider | None, request: ExecutorRequest) -> SummaryRunner | None:
    """摘要模型调用：只在开了摘要、且**冻结的摘要模型**与它的 provider 都在时才装。

    摘要跑在自己那条模型定义上（endpoint / 模型名 / 凭据都不同，ADR-06 的 `model_ref`）。
    缺模型或缺凭据就返回 None ⇒ 这一层本轮不跑；**绝不退回主模型**——那正是修复前的行为
    （`model_ref` 写了却不生效，2026-10-06 review）。
    """
    settings = request.compaction.summary if request.compaction is not None else None
    if settings is None or not settings.enabled or not settings.model_ref:
        return None
    model = request.summary_model
    if model is None or provider is None:
        return None
    return make_summary_runner(provider=provider, model_id=model.model_id)


async def _model_provider(
    model: ResolvedModel,
    *,
    timeout_sec: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelProvider:
    """按**一条模型定义**建 provider：endpoint / 模型名 / 凭据全部取自它自己那一份。"""
    return OpenAICompatibleProvider(
        base_url=model.base_url,
        model=model.model_id,
        api_key=await resolve_model_api_key(model),
        timeout_sec=timeout_sec,
        transport=transport,
    )


def _pooled_provider_for(
    request: ExecutorRequest,
    *,
    scope: PoolCallScope,
    audit_writer: RuntimeAuditWriter | None,
    model_trace: ModelCallTrace | None,
    timeout_sec: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PooledModelProvider:
    """按生产装配口径构造池化 provider：真实分配器/容量服务/受信解析客户端/模型 HTTP。

    分配器与续租/结算**共用同一个容量服务**：额度判定与窗口算法只有一处实现
    （`ModelCapacityService`），池化链路不复制它。每次尝试的上游 provider 由
    `provider_factory` 按解析出的凭据（连接 + 密钥）新建——凭据只进调用内存。
    """
    credentials = request.credentials_client
    assert credentials is not None  # `_pool_scope_for` 已要求
    capacity = ModelCapacityService(get_session_factory)
    return PooledModelProvider(
        scope=scope,
        allocator=ModelAllocator(get_session_factory, capacity=capacity),
        capacity=capacity,
        credentials=credentials,
        provider_factory=lambda credential: OpenAICompatibleProvider(
            base_url=credential.base_url,
            model=request.model.model_id,
            api_key=credential.api_key.get_secret_value(),
            timeout_sec=timeout_sec,
            transport=transport,
        ),
        model=request.model.model_id,
        audit_writer=audit_writer,
        model_trace=model_trace,
    )


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
        session_factory=get_session_factory,
        credential_canaries=tuple(value for value in
            (request.model.api_key, *request.credentials.mcp_secrets.values()) if value),
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
