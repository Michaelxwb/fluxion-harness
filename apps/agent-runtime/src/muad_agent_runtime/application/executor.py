from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol, cast

import httpx
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    RunnerCancelled,
)
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import (
    ModelBudget,
    ModelContent,
    ModelMessage,
    ModelProvider,
    ModelRateLimitedError,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelUnavailableError,
    OpenAICompatibleProvider,
    StreamingModelProvider,
    text_of,
)
from muad_agent_core.prompt import PromptSkill
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
from muad_contracts.platform_settings import (
    ArtifactSettings,
    CompactionSettings,
    LocaleSettings,
    MemoryPolicySettings,
    ToolResultSettings,
)
from muad_logging.redaction import redact_text
from muad_platform_sdk.types import SecretValue

from ..infrastructure.audit_writer import RuntimeAuditWriter
from ..infrastructure.db import get_session_factory
from ..infrastructure.gateway_delivery_client import GatewayDeliveryClient
from ..metrics import MODEL_INVOCATIONS_METRIC, TOOL_CALLS_METRIC, record_outcome
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
from .mcp_runtime_adapter import McpRuntimeAdapter, McpServerDefinition, McpToolDefinition
from .memory_service import MemoryService
from .memory_tools import MemoryScope, MemoryToolSet, memory_write_enabled
from .skill_tools import build_default_skill_cache, build_skill_registry
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

        async def on_model_started() -> None:
            await emit(ExecutorEvent(type=MODEL_STARTED_EVENT, data={}))

        async def on_model_completed() -> None:
            await emit(ExecutorEvent(type=MODEL_COMPLETED_EVENT, data={}))

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
                yield item
            await task
            result = task.result()
            if not delta_emitted and result is not None and result.final_text:
                # 非流式 provider：把最终回答作为单个 delta，保证渠道侧拼接可用
                yield ExecutorEvent(type=MESSAGE_DELTA_EVENT, data={"delta": result.final_text})
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
    ) -> Any:
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
            skills=tuple(_prompt_skill(skill) for skill in self._request.skills),
            messages=messages,
            policy=agent_policy_for(self._request),
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


def _prompt_skill(skill: ResolvedSkill) -> PromptSkill:
    """把生效 Skill 投影成提示词目录项（只带名称/描述，产物细节不进提示词）。

    缺了这一步，`DefaultPromptBuilder` 的 `## Available skills` 段永远为空 —— 模型不知道
    自己有哪些技能可按名加载（症状：用户自然语言问"你有哪些技能"时，模型只能答"我无法
    列出，请告诉我 key"）。授权判定仍在上游（生效集合），本函数只做投影。
    """
    label = skill.frontmatter.get("platform_label")
    return PromptSkill(
        key=skill.key,
        name=skill.name,
        description=skill.description,
        platform_label=str(label) if label else None,
    )


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
    ) -> None:
        self._context = context
        self._audit = audit_writer
        self._artifacts = artifact_writer
        self._settings = settings or ToolResultSettings()
        self._round: dict[str, _RoundCall] = {}

    async def __call__(
        self,
        definition: ToolDefinition,
        arguments: Mapping[str, Any],
        handler: Any,
        *,
        call_id: str,
    ) -> str:
        """执行一次工具并**只做缓冲**：落盘与否要等本轮结果到齐（ADR-04）。"""
        started_wall = datetime.now(UTC)
        started = time.monotonic()
        status = "OK"
        error_code: str | None = None
        try:
            return str(await handler(arguments, call_id=call_id))
        except Exception as exc:
            status = "ERROR"
            error_code = _tool_error_code(exc)
            raise
        finally:
            record_outcome(
                TOOL_CALLS_METRIC,
                status,
                {"kind": _tool_kind(definition.name), "tool": definition.name},
            )
            self._round[call_id] = _RoundCall(
                definition=definition,
                status=status,
                error_code=error_code,
                started_wall=started_wall,
                latency_ms=int((time.monotonic() - started) * 1000),
                args_hash=_args_hash(arguments),
                args_preview=_preview_args(arguments),
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
            (call_ids[index], self._tool_name(call_ids[index]), contents[index])
            for index in sorted(selected)
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

    async def _flush_audit(
        self, call_ids: list[str], references: Mapping[str, Mapping[str, Any]]
    ) -> None:
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


def _preview_args(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """工具的入参预览（落 `runtime.tool_call_audit.args_preview_json`）。

    **先截断、再过 `redact_text`**：预览里装的是**模型自己写的内容**——
    `write_artifact(content=...)`、`create_archive(files=[{content: ...}])` 都会把正文开头塞进来。
    模型完全可能在文件里写一段带 `api_key=…`/`token=…` 的配置，而那 200 字符会**落进审计表**。
    `RULE-secret-001` 要求密钥不得进入日志与审计，所以这里复用**同一套**脱敏策略
    （`muad_logging.redaction`）——不是另写一份扫密钥逻辑（那才会两处漂移）。

    顺序是「先截断后脱敏」：先脱敏意味着对一个可能几 MB 的值跑正则，代价与收益不成比例；
    200 字符窗口内**完整出现**的键值对已能被覆盖，而这正是实际的泄漏形态。
    """
    preview: dict[str, Any] = {}
    for key, value in arguments.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        preview[str(key)] = redact_text(text[:200])
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
        wrapped.register(replace(definition, handler=partial(recorder, definition, handler=handler)))
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
    delivery_client: GatewayDeliveryClient | None = None,
    recorder: ToolCallRecorder | None = None,
) -> ToolRegistry:
    policy = agent_policy_for(request)
    execution = execution_defaults_for(request)
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
            )
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
        # 记忆工具的作用域只来自 Run 上下文（`user_id`/`tenant_id` 不进工具 schema）；
        # `memory_write=false` 的 agent 连 `remember` 都看不见，但 `recall` 仍注册——
        # 注入面由 ContextBuilder 统一治理，读侧不随写开关变化。
        MemoryToolSet(
            service=MemoryService(),
            scope=MemoryScope(
                tenant_id=request.run_context.tenant_id,
                user_id=request.run_context.user_id,
                run_id=request.run_context.run_id,
            ),
            write_enabled=memory_write_enabled(
                request.agent.runtime_config, default=execution.memory_policy.write_enabled
            ),
            recall_default_limit=execution.memory_policy.recall_default_limit,
            max_recall_bytes=execution.memory_policy.max_recall_bytes,
        ).register(registry)
    if request.run_context is not None:
        registry = _wrap_registry(
            registry,
            recorder
            or ToolCallRecorder(
                context=request.run_context,
                audit_writer=audit_writer,
                artifact_writer=artifact_writer,
                settings=tool_result_settings(request),
            ),
        )
    return registry


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
    budget = ModelBudget(
        deadline_ms=frozen.deadline_ms, max_model_retries=frozen.max_model_retries
    )
    provider: ModelProvider = await _model_provider(
        request.model, timeout_sec=budget.request_timeout_sec(), transport=model_transport
    )
    audit_writer = _audit_writer_for(request)
    if audit_writer is not None:
        provider = AuditedModelProvider(provider, audit_writer, model=request.model.model_id)
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
            owned_delivery_client = GatewayDeliveryClient(settings.im_gateway_url)
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
        )
    registry = build_registry(
        request=request,
        cache=skill_cache or build_default_skill_cache(),
        audit_writer=audit_writer,
        mcp_adapter=mcp_adapter,
        artifact_writer=artifact_writer,
        task_client=task_client,
        delivery_client=delivery_client,
        recorder=recorder,
    )
    runner = AgentRunner(
        provider=provider,
        registry=registry,
        hooks=HookPipeline(),
        context_compactor=_context_compactor(
            request, summary_provider, artifact_root=settings.artifact_root
        ),
        tool_round_results=recorder,
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

    return AgentRunnerExecutor(runner=runner, request=request, close=close)


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


def _summary_runner(
    provider: ModelProvider | None, request: ExecutorRequest
) -> SummaryRunner | None:
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
