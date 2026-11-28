"""共享 Agent 执行内核（design §3.1.2 / §3.2.4）。

**一份循环，两个宿主。** 本模块只依赖 agent-core 自身与共享 contracts；所有宿主差异都在适配器
提供的端口上，Kernel 不 import 任一 app、ORM 或渠道适配器，也不按宿主类型分支。

执行输入 `AgentRunRequest` 就是 design 的 `ExecutionSpec`：冻结的模型、指令、技能、策略、预算与
截止时间。端口清单：

| 端口 | 类型 | 宿主提供什么 |
|---|---|---|
| 模型 | `ModelProvider` | 已构造好的 provider：模型池/凭据/容量由适配器解析 |
| 工具 | `ToolRegistry` | 只构造 allowlist 内的工具（越权 → `TOOL_NOT_ALLOWED`） |
| 记忆（只读） | `ModelContextLoader` | 冻结 revision 的注入块；定时任务不写长期记忆 |
| 事件/检查点 | `ExecutionEventPort` | Runtime→canonical_event，Worker→task_event |
| 挂起/恢复 | `ToolSuspensionPort` | 中立 `SuspensionRequest`：等人回答或等外部操作 |
| 压缩 | `ContextCompactor` | 分层压缩；失败退回原历史（见 `_compacted`） |
| 整轮外置 | `ToolResultRoundPort` | 产物写入；整轮失败保持内联（见 `_round_results_or_inline`） |
| 取消/截止 | `is_cancelled` + `deadline_at` | lease 失效、cancel_requested、绝对 deadline |
| 校验 | `answer_verifier` | 输出契约的 verifier |
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, TypedDict, cast

from muad_contracts.platform_settings import MAX_IDENTICAL_FAILURES, MIN_IDENTICAL_FAILURES
from muad_logging import get_logger

from ..context.compactor import ContextCompactor
from ..context.plan import RunInput, plan_context, render_context
from ..hooks.pipeline import HookEvent, HookPipeline
from ..memory.components import MemoryComponent, ModelContextLoader
from ..memory.language import resolve_reply_locale
from ..model.budget import (
    DEFAULT_DEADLINE_MS,
    DEFAULT_MAX_IDENTICAL_FAILURES,
    DEFAULT_MAX_MODEL_RETRIES,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_MAX_TURNS,
    ModelBudget,
)
from ..model.errors import (
    ModelRateLimitedError,
    ModelRequestError,
    ModelUnavailableError,
)
from ..model.provider import (
    DeltaCallback,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelUsage,
    text_of,
)
from ..prompt.builder import DefaultPromptBuilder, PromptBuilder, PromptSkill
from ..tools.identity import AssistantTurn, PreparedCall, ToolCall
from ..tools.outcome import (
    AgentDisposition,
    BusinessStatus,
    ExecutionOutcome,
    RetryDisposition,
    tool_exception_outcome,
)
from ..tools.pipeline import (
    PreparedToolCall,
    RunnableToolCall,
    ToolExecutionPipeline,
    build_prepared_call,
    compute_args_hash,
)
from ..tools.planner import (
    DEFAULT_PARALLEL_LIMIT,
    PlannedToolCall,
    ToolBatch,
    plan_tool_batches,
    validate_parallel_limit,
)
from ..tools.registry import ToolDefinition, ToolNotFoundError, ToolRegistry
from ..tools.result import (
    ToolCompleted,
    ToolError,
    ToolExecutionResult,
    ToolResult,
    completed_from_result,
    failure_result,
)
from ..tools.round_results import ToolResultRoundPort
from .continuation import ExecutionEventPort, RunnerCheckpoint, WaitDecision
from .failure import (
    RETRY_GATE_BLOCKED_ATTEMPT,
    RETRY_GATE_GUARDED,
    RETRY_GATE_UNVERIFIED_WRITE,
    FailureSignal,
    attempt_identity,
    failure_scope,
    is_effectful,
    is_hard_tool_failure,
    unverified_write_sources,
    update_failure_state,
)
from .suspension import SuspensionKind, SuspensionRequest, ToolSuspensionPort
from .termination import (
    BudgetKind,
    FailureEvidence,
    FailureState,
    PendingToolCall,
    TerminationSnapshot,
    now_iso,
)

logger = get_logger("muad_agent_core.agent.runner")

NODE_PREPARE_CONTEXT = "prepare_context"
NODE_MODEL = "model"
NODE_TOOLS = "tools"
NODE_FINALIZE = "finalize"
NODE_DECIDE = "decide"
TOOL_BUDGET_EXHAUSTED = "tool call budget exhausted"
TOOL_NOT_EXECUTED_CODE = "TOOL_NOT_EXECUTED"
TOOL_NOT_EXECUTED = "tool call not executed after a deterministic failure"
UNKNOWN_TOOL_TEMPLATE = "unknown tool: {name}"
#: 空回复的补答指令：只陈述"没有正文"这个事实，不猜原因——模型在这一步没有可解释的事实。
EMPTY_REPLY_NUDGE = (
    "你上一轮没有输出任何正文。请直接给出结论；如果工具调用失败了，如实说明失败原因与下一步。"
)
TOOL_BLOCKED_TEMPLATE = "tool blocked before execution: {reason}"
TOOL_FAILED_TEMPLATE = "tool failed: {reason}"


def tool_failure_content(exc: BaseException) -> str:
    """工具抛异常 → 喂回模型的工具消息。
    **必须与工具自己「返回」的错误体同形**：`skill_tools`/`task_tools`/`memory_tools` 的处理
    器在自己那层就把领域错误转成 `{"error": {"code", "message"}}` 返回，而 attachments/archive
    是**抛出**带 `.code` 的自己人错误类型。后者若只拿到 `tool failed: AttachmentToolError: …`，
    模型面对的就是**两种形状、且看不到错误码** —— 同一件事两套说法（2026-10-03 review）。

    没有 `.code` 的异常（真正的意外）保持原文案：那才是"工具炸了"，与"工具拒绝了你"不同。

    `AppError` 属前者但**没有用户可见 message**（只带 `code` + `message_args`，正文由 catalog 在
    API 边界按 locale 解析），故 message 退化成 code —— 与 `task_tools` 既有写法
    （`_error(str(exc.code), str(exc.code))`）一致，agent-core 也拿不到 catalog。
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        message = getattr(exc, "message", None) or str(exc)
        error: dict[str, object] = {"code": code, "message": str(message)}
        outcome_known = getattr(exc, "outcome_known", True) is True
        if getattr(exc, "retryable", False) and outcome_known:
            error["retryable"] = True
        if not outcome_known:
            error["outcome_known"] = False
        return json.dumps({"error": error}, ensure_ascii=False)
    return TOOL_FAILED_TEMPLATE.format(reason=f"{type(exc).__name__}: {exc}")


class RunnerError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message
        #: 终止快照由 Kernel 顶层边界挂上；Runtime 用同一份事实收尾（design §4.3）。
        self.termination: TerminationSnapshot | None = None
        super().__init__(message)


class RunnerModelError(RunnerError):
    pass


class RunnerDeadlineExceeded(RunnerError):
    pass


class RunnerCancelled(RunnerError):
    pass


class RunnerVerificationExhausted(RunnerError):
    """候选在验证上限内全部未通过；Run 不得输出未验证答案。"""


class RunnerEmptyReply(RunnerError):
    """模型补答后仍没有输出任何正文：空回答不是"完成"，Run 必须以失败收口。"""


class RunnerShutdown(RunnerError):
    """宿主关闭/进程退出：Run 未完成，按 RUN_ERROR 收口。"""


class AgentRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_INPUT = "WAITING_INPUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    #: 同一 (工具, 错误码) 连续失败达到上限：确定性失败的原地打转，继续尝试没有意义。
    NO_PROGRESS = "NO_PROGRESS"
    #: 工具失败未解决，模型不能继续改用其他执行方式。
    TOOL_FAILURE = "TOOL_FAILURE"


#: 挂起收口状态：`WAITING_INPUT`（等用户回答）与 `WAITING_TOOL`（等外部操作结果）。
#: 两者都**不是失败**，收尾阶段不得被预算/无进展判定改写。
_SUSPENDED_STATUSES = frozenset(
    (str(AgentRunStatus.WAITING_INPUT), str(AgentRunStatus.WAITING_TOOL))
)


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    max_turns: int = DEFAULT_MAX_TURNS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    deadline_ms: int = DEFAULT_DEADLINE_MS
    max_model_retries: int = DEFAULT_MAX_MODEL_RETRIES
    max_identical_failures: int = DEFAULT_MAX_IDENTICAL_FAILURES

    def __post_init__(self) -> None:
        if not MIN_IDENTICAL_FAILURES <= self.max_identical_failures <= MAX_IDENTICAL_FAILURES:
            raise ValueError(
                "max_identical_failures must be between "
                f"{MIN_IDENTICAL_FAILURES} and {MAX_IDENTICAL_FAILURES}"
            )

    @classmethod
    def from_runtime_config(
        cls, config: Mapping[str, Any], *, base: AgentPolicy | None = None
    ) -> AgentPolicy:
        """从 Agent runtime_config 读覆盖；`base` 给出底层默认（缺省用平台设置/ schema 默认）。

        平台设置冻结值经 `base` 注入，Agent 自身的 runtime_config 覆盖优先级更高。
        """
        defaults = base if base is not None else cls()
        return cls(
            max_turns=_limit(config, "max_turns", defaults.max_turns),
            max_tool_calls=_limit(config, "max_tool_calls", defaults.max_tool_calls),
            deadline_ms=_limit(config, "deadline_ms", defaults.deadline_ms),
            max_model_retries=_limit(config, "max_model_retries", defaults.max_model_retries),
            max_identical_failures=_limit(
                config, "max_identical_failures", defaults.max_identical_failures
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    """design 的 `ExecutionSpec`：一次执行冻结下来的全部输入（恢复时原样重用，不重新解析）。"""

    model_id: str
    instructions: str
    messages: tuple[ModelMessage, ...] = ()
    skills: tuple[PromptSkill, ...] = ()
    policy: AgentPolicy = AgentPolicy()
    #: 平台默认回复语言；None = 旧 Run/直构路径不追加语言段。
    reply_locale: str | None = None
    #: 定时任务冻结的用户 `PREFERENCE/language`；Runner 与平台默认统一裁决。
    memory_language: str | None = None
    #: 当前 Run 的显式输入；缺失时由宿主消息边界推导一次（不在 ContextPlan 内推断）。
    run_input: RunInput | None = None
    #: 历史装配的 seq 元数据；摘要/裁剪只读这些事实，不在 plan 内重新推断。
    covered_seq: int = 0
    material_scan_end_seq: int = 0
    head_end_seq: int | None = None
    earlier_records: int | None = 0
    #: 已成功 load_skill 的 key；恢复时由宿主重建后注入。
    active_skills: tuple[str, ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    deadline_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    final_text: str
    status: AgentRunStatus
    turns: int
    tool_calls: int
    usage: ModelUsage
    #: 最近一次失败（工具/错误码/消息）；NO_PROGRESS 收尾时用于解释原因。
    last_failure: dict[str, str] | None = None
    #: 终止事实：Runtime 的 facts/renderer 只读它，不再解析 Kernel 的进程内 state。
    termination: TerminationSnapshot | None = None
    reply_append: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerCallbacks:
    is_cancelled: Callable[[], bool] | None = None
    on_delta: DeltaCallback | None = None
    on_tool_prepared: Callable[[PreparedCall], Awaitable[None]] | None = None
    on_model_started: Callable[[], Awaitable[None]] | None = None
    on_model_completed: Callable[[], Awaitable[None]] | None = None
    on_assistant_turn: Callable[[AssistantTurn], Awaitable[None]] | None = None
    on_tool_completed: Callable[[ToolCompleted], Awaitable[None]] | None = None


class AgentRunState(TypedDict):
    model_id: str
    instructions: str
    skills: list[PromptSkill]
    policy: AgentPolicy
    temperature: float | None
    max_tokens: int | None
    params: dict[str, Any]
    messages: list[ModelMessage]
    pending_tool_calls: list[ToolCall]
    run_input: RunInput | None
    covered_seq: int
    material_scan_end_seq: int
    head_end_seq: int | None
    earlier_records: int | None
    active_skills: set[str]
    final_text: str
    turns: int
    tool_calls_used: int
    input_tokens: int | None
    output_tokens: int | None
    budget_exhausted: bool
    window_limited: bool
    window_limit_reply: str | None
    reply_append: str | None
    assistant_tool_text: str
    #: 同一 (工具, 错误码) 的连续失败计数；任一工具成功即清零（"有进展"）。
    identical_failures: dict[str, int]
    failure_pending: bool
    blocked_attempts: frozenset[str]
    blocked_attempt_sources: Mapping[str, str]
    unknown_write_targets: frozenset[str]
    unknown_target_sources: Mapping[str, str]
    unknown_write_operations: Mapping[str, frozenset[str]]
    unknown_operation_sources: Mapping[str, str]
    reviews: dict[str, tuple[int, int]]
    #: 失败重复次数达到上限：停止工具派发，普通失败可在剩余预算内解释。
    no_progress: bool
    #: 第一次触发 no_progress 的调用与它指向的业务失败（门禁触发时不同）。
    no_progress_trigger_call_id: str
    no_progress_source_call_id: str
    #: 最近一次失败的事实（结构化 evidence），收尾 facts 与兜底文案都读它。
    last_failure: FailureEvidence | None
    #: 因预算/终止而未执行的调用：只进 snapshot 的 pending，不进入失败计数。
    unexecuted_tool_calls: list[PendingToolCall]
    status: str
    started_at: float
    is_cancelled: Callable[[], bool] | None
    on_delta: DeltaCallback | None
    on_tool_prepared: Callable[[PreparedCall], Awaitable[None]] | None
    on_model_started: Callable[[], Awaitable[None]] | None
    on_model_completed: Callable[[], Awaitable[None]] | None
    # 每次模型响应触发一次，带原始 assistant 消息与内部调用身份：
    # 调用方据此把「一个 assistant 回合」原样持久化，供后续 Run 重建**合法**历史。
    on_assistant_turn: Callable[[AssistantTurn], Awaitable[None]] | None
    #: typed 完成事实：正文、outcome、产物 role 与 follow-up 引用一起交给宿主。
    on_tool_completed: Callable[[ToolCompleted], Awaitable[None]] | None
    last_response: dict[str, Any] | None
    post_model_pending: bool
    deadline_at: datetime | None
    consumed_event_seq: int
    injected_event_seq: int
    next_node: str


def _limit(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _usage_sum(previous: int | None, observed: int | None) -> int | None:
    return None if previous is None or observed is None else previous + observed


def _tool_message(call_id: str, content: str) -> ModelMessage:
    return ModelMessage(role=ModelRole.TOOL, content=content, tool_call_id=call_id)


def _suspension_status(request: SuspensionRequest) -> AgentRunStatus:
    """挂起的收口状态：等人回答 → `WAITING_INPUT`；等外部操作 → `WAITING_TOOL`。"""
    if request.kind is SuspensionKind.TOOL_OPERATION:
        return AgentRunStatus.WAITING_TOOL
    return AgentRunStatus.WAITING_INPUT


#: 统一执行入口的终态 → 回合事件状态（工具/审计两侧共用同一映射）。
_TOOL_STATUSES = {
    "SUCCEEDED": "OK",
    "FAILED": "ERROR",
    "POLICY_DENIED": "BLOCKED",
    "BLOCKED": "BLOCKED",
    "NOT_FOUND": "NOT_FOUND",
}


@dataclass(frozen=True, slots=True)
class _CallOutcome:
    """一次调用在原始调用序上的最终形态（失败也只是一条失败 ToolResult）。"""

    call: ToolCall
    status: str
    result: ToolResult
    prepared: PreparedCall | None = None
    handler_entered: bool = False

    @property
    def content(self) -> str:
        return self.result.content

    @property
    def outcome(self) -> ExecutionOutcome:
        return self.result.outcome

    @property
    def follow_ups(self) -> tuple[ModelMessage, ...]:
        return self.result.follow_ups


def _failure_state_of(state: AgentRunState) -> FailureState:
    return FailureState(
        last_failure=state["last_failure"],
        failure_pending=state["failure_pending"],
        identical_failures=state["identical_failures"],
        blocked_attempts=state["blocked_attempts"],
        blocked_attempt_sources=state["blocked_attempt_sources"],
        unknown_write_targets=state["unknown_write_targets"],
        unknown_target_sources=state["unknown_target_sources"],
        unknown_write_operations=state["unknown_write_operations"],
        unknown_operation_sources=state["unknown_operation_sources"],
        reviews=state["reviews"],
        no_progress=state["no_progress"],
        no_progress_trigger_call_id=state["no_progress_trigger_call_id"],
        no_progress_source_call_id=state["no_progress_source_call_id"],
    )


def _attempt_of(prepared: PreparedCall) -> str:
    scope = failure_scope(prepared.call.name, skill_key=prepared.skill_key, skill_id=prepared.skill_id)
    return attempt_identity(scope, prepared.args_hash)


def _diagnostic_source(diagnostic: Mapping[str, object] | None) -> str:
    if not diagnostic:
        return ""
    value = diagnostic.get("source_tool_call_id")
    return value if isinstance(value, str) else ""


def _diagnostic_owner(diagnostic: Mapping[str, object] | None) -> str | None:
    if not diagnostic:
        return None
    value = diagnostic.get("action_owner")
    return value if isinstance(value, str) and value in {"USER", "ADMIN", "NONE"} else None


def _diagnostic_hint_known(diagnostic: Mapping[str, object] | None) -> bool | None:
    if not diagnostic:
        return None
    value = diagnostic.get("hint_known")
    return value if isinstance(value, bool) else None


def _host_owned_failure(state: Mapping[str, Any]) -> bool:
    failure = state.get("last_failure")
    return bool(
        state.get("failure_pending")
        and failure is not None
        and (
            failure.action_owner in {"ADMIN", "NONE"}
            or (failure.hint_known is True and failure.action_owner is None)
        )
    )


def _exception_outcome(exc: BaseException, *, handler_entered: bool = False) -> ExecutionOutcome:
    """执行器边界上的异常 → TOOL 失败 outcome（与 `tool_failure_content` 同一错误码来源）。"""
    return tool_exception_outcome(
        code=getattr(exc, "code", None),
        message=str(exc),
        retryable=getattr(exc, "retryable", None),
        retry_after=getattr(exc, "retry_after", None),
        status_code=getattr(exc, "status_code", None),
        sent=getattr(exc, "sent", False) is True,
        outcome_known=getattr(exc, "outcome_known", not handler_entered) is True,
    )


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except ValueError:
            return None
        retry_at = retry_at.replace(tzinfo=retry_at.tzinfo or UTC)
        delay = max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
    return delay if math.isfinite(delay) and delay >= 0 else None


def _artifact_ref(content: str) -> str | None:
    """工具结果被外置为 Artifact 时，从引用 JSON 中取 artifact_id（用于 tool.completed 事件）。"""
    if not content.startswith("{"):
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    artifact = payload.get("artifact")
    if isinstance(artifact, dict) and isinstance(artifact.get("artifact_id"), str):
        return str(artifact["artifact_id"])
    return None


class AgentRunner:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        registry: ToolRegistry,
        hooks: HookPipeline | None = None,
        prompt_builder: PromptBuilder | None = None,
        context_compactor: ContextCompactor | None = None,
        tool_round_results: ToolResultRoundPort | None = None,
        tool_pipeline: ToolExecutionPipeline | None = None,
        runtime_events: ExecutionEventPort | None = None,
        tool_suspension: ToolSuspensionPort | None = None,
        context_loader: ModelContextLoader | None = None,
        event_batch_size: int = 16,
        parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
        answer_verifier: Callable[[str], Awaitable[tuple[bool, str]]] | None = None,
        verification_max_attempts: int = 1,
        window_limit_reply: Callable[[str | None], str] | None = None,
    ) -> None:
        if verification_max_attempts < 1:
            raise ValueError("verification_max_attempts must be >= 1")
        self._answer_verifier = answer_verifier
        self._verification_max_attempts = verification_max_attempts
        self._window_limit_reply = window_limit_reply
        self._provider = provider
        self._registry = registry
        self._hooks = hooks or HookPipeline()
        self._tool_pipeline = tool_pipeline or ToolExecutionPipeline(registry=registry, hooks=self._hooks)
        self._prompt_builder = prompt_builder or DefaultPromptBuilder()
        # 压缩只作用于**派生请求**（design §3.1 ADR-01：这里是 `ModelRequest` 的唯一组装点），
        # `state["messages"]` 这份权威历史一个字节都不动。
        self._context_compactor = context_compactor
        # 工具结果的整批判定发生在**回合末**（design ADR-04）：外置与否要看本轮全部结果的大小，
        # 且产物 id 必须赶在 `tool.completed` 之前定下来。
        self._round_results = tool_round_results
        self._runtime_events = runtime_events
        self._tool_suspension = tool_suspension
        self._context_loader = context_loader
        #: 启动时预取的记忆块：同一份既决定提示词默认语言，也注入首个模型请求，
        #: 避免为了语言偏好重复读取/重复归因。
        self._startup_component: MemoryComponent | None = None
        self._startup_component_pending = False
        self._event_batch_size = event_batch_size
        self._parallel_limit = validate_parallel_limit(parallel_limit)
        self._stop_emitted = False
        self._latest_state: AgentRunState | None = None

    async def run(
        self,
        request: AgentRunRequest,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        on_delta: DeltaCallback | None = None,
        on_tool_prepared: Callable[[PreparedCall], Awaitable[None]] | None = None,
        on_model_started: Callable[[], Awaitable[None]] | None = None,
        on_model_completed: Callable[[], Awaitable[None]] | None = None,
        on_assistant_turn: Callable[[AssistantTurn], Awaitable[None]] | None = None,
        on_tool_completed: Callable[[ToolCompleted], Awaitable[None]] | None = None,
        checkpoint: RunnerCheckpoint | None = None,
        context_cursor: int | None = None,
    ) -> AgentRunResult:
        callbacks = RunnerCallbacks(
            is_cancelled,
            on_delta,
            on_tool_prepared,
            on_model_started,
            on_model_completed,
            on_assistant_turn,
            on_tool_completed,
        )
        startup_component = await self._load_startup_component()
        initial = self._initial_state(
            request,
            checkpoint or RunnerCheckpoint(),
            callbacks,
            context_cursor,
            startup_component=startup_component,
        )
        self._latest_state = initial
        try:
            final = await self._execute(initial)
        except RunnerError as exc:
            # 所有异常出口挂同一份 snapshot：Runtime 不依赖进程内 state，也不猜失败事实。
            if exc.termination is None:
                exc.termination = self._snapshot(self._latest_state)
            await self._emit_stop_hook(final_text="", turns=self._latest_state["turns"], error=True)
            raise
        except Exception:
            await self._emit_stop_hook(final_text="", turns=self._latest_state["turns"], error=True)
            raise
        return AgentRunResult(
            final["final_text"],
            AgentRunStatus(final["status"]),
            final["turns"],
            final["tool_calls_used"],
            ModelUsage(input_tokens=final["input_tokens"], output_tokens=final["output_tokens"]),
            last_failure=(
                final["last_failure"].as_legacy_dict() if final["last_failure"] else None
            ),
            termination=self._snapshot(final),
            reply_append=final["reply_append"],
        )

    async def _emit_stop_hook(self, *, final_text: str, turns: int, error: bool) -> None:
        if self._stop_emitted:
            return
        self._stop_emitted = True
        payload: dict[str, Any] = {"final_text": final_text, "turns": turns}
        if error:
            payload["error"] = True
        await self._hooks.run(HookEvent.STOP, payload)

    async def _load_startup_component(self) -> MemoryComponent | None:
        """Load memory once before the prompt is built.

        The prompt's default language must be resolved from the same frozen
        memory block that will be injected into the first model request; doing
        it later would let the prompt and the memory message disagree.
        """
        if self._context_loader is None:
            return None
        component = await self._context_loader.refresh()
        self._startup_component = component
        self._startup_component_pending = True
        return component

    def _initial_state(
        self,
        request: AgentRunRequest,
        progress: RunnerCheckpoint,
        callbacks: RunnerCallbacks,
        context_cursor: int | None,
        *,
        startup_component: MemoryComponent | None = None,
    ) -> AgentRunState:
        memory_locale = request.memory_language
        if memory_locale is None and startup_component is not None:
            memory_locale = startup_component.language
        effective_locale = resolve_reply_locale(request.reply_locale, memory_locale)
        system = self._prompt_builder.build(
            instructions=request.instructions,
            skills=request.skills,
            reply_locale=effective_locale,
        )
        run_input = request.run_input
        restored = FailureState.from_payload(progress.failure_state)
        window_reply = self._window_limit_reply(effective_locale) if self._window_limit_reply else None
        recovered_limit = progress.recovered_window_limit and window_reply is not None
        return AgentRunState(
            model_id=request.model_id,
            instructions=request.instructions,
            skills=list(request.skills),
            policy=request.policy,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            params=dict(request.params),
            messages=[ModelMessage(role=ModelRole.SYSTEM, content=system), *request.messages],
            pending_tool_calls=[],
            run_input=run_input,
            covered_seq=request.covered_seq,
            material_scan_end_seq=request.material_scan_end_seq,
            head_end_seq=request.head_end_seq,
            earlier_records=request.earlier_records,
            active_skills=set(request.active_skills),
            final_text=progress.recovered_window_text if recovered_limit else "",
            turns=progress.turns,
            tool_calls_used=progress.tool_calls,
            input_tokens=progress.input_tokens,
            output_tokens=progress.output_tokens,
            budget_exhausted=False,
            window_limited=recovered_limit,
            window_limit_reply=window_reply,
            reply_append=None,
            assistant_tool_text=progress.recovered_window_text if recovered_limit else "",
            identical_failures=dict(restored.identical_failures),
            failure_pending=restored.failure_pending,
            blocked_attempts=restored.blocked_attempts,
            blocked_attempt_sources=dict(restored.blocked_attempt_sources),
            unknown_write_targets=restored.unknown_write_targets,
            unknown_target_sources=dict(restored.unknown_target_sources),
            unknown_write_operations=restored.unknown_write_operations,
            unknown_operation_sources=dict(restored.unknown_operation_sources),
            reviews=dict(restored.reviews),
            no_progress=restored.no_progress,
            no_progress_trigger_call_id=restored.no_progress_trigger_call_id,
            no_progress_source_call_id=restored.no_progress_source_call_id,
            last_failure=restored.last_failure,
            unexecuted_tool_calls=[],
            status=str(AgentRunStatus.COMPLETED),
            started_at=time.monotonic(),
            is_cancelled=callbacks.is_cancelled,
            on_delta=callbacks.on_delta,
            on_tool_prepared=callbacks.on_tool_prepared,
            on_model_started=callbacks.on_model_started,
            on_model_completed=callbacks.on_model_completed,
            on_assistant_turn=callbacks.on_assistant_turn,
            on_tool_completed=callbacks.on_tool_completed,
            last_response=None,
            post_model_pending=False,
            deadline_at=request.deadline_at,
            consumed_event_seq=progress.consumed_event_seq,
            injected_event_seq=context_cursor if context_cursor is not None else progress.consumed_event_seq,
            next_node=NODE_FINALIZE,
        )

    async def _execute(self, state: AgentRunState) -> AgentRunState:
        """按阶段推进一次 Run；去向与预算出口全部由既有路由函数决定。

        工具止损后只允许基于现有事实给出解释，宿主负责硬停止的收尾。
        """
        if state["window_limited"]:
            self._ensure_runnable(state)
            return await self._finalize(state)
        while True:
            state = self._remember(await self._prepare_context(state))
            prepared = self._route_after_prepare(state)
            if prepared == NODE_FINALIZE:
                break
            state = self._remember(await self._call_model(state))
            route = self._route_after_model(state)
            if route == NODE_TOOLS:
                state = self._remember(await self._execute_tools(state))
                after_tools = self._route_after_tools(state)
                if after_tools == NODE_FINALIZE:
                    break
                continue
            if route == NODE_DECIDE:
                state = self._remember(await self._decide_after_model(state))
                if state["next_node"] == NODE_FINALIZE:
                    break
                continue
            break
        return await self._finalize(state)

    def _remember(self, state: AgentRunState) -> AgentRunState:
        """记录最新状态：异常出口要用它生成 snapshot（state 在循环里被替换成新 dict）。"""
        self._latest_state = state
        return state

    async def notify_interrupt(self, payload: dict[str, Any]) -> None:
        """澄清/确认暂停等 interrupt 事件：触发 on_interrupt Hook（可观测）。"""
        await self._hooks.run(HookEvent.ON_INTERRUPT, dict(payload))

    async def _prepare_context(self, state: AgentRunState) -> AgentRunState:
        self._ensure_runnable(state)
        if state["turns"] >= state["policy"].max_turns:
            return {**state, "budget_exhausted": True}
        state = await self._drain_events(state)
        context = await self._hooks.run(
            HookEvent.USER_PROMPT,
            {"messages": state["messages"], "turns": state["turns"]},
        )
        messages = context.payload.get("messages", state["messages"])
        if not isinstance(messages, (list, tuple)):
            raise RunnerError("user_prompt hook returned invalid messages")
        return {**state, "messages": list(messages)}

    async def _call_model(self, state: AgentRunState) -> AgentRunState:
        self._ensure_runnable(state)
        await self._hooks.run(
            HookEvent.PRE_MODEL,
            {"model_id": state["model_id"], "messages": state["messages"], "turns": state["turns"]},
        )
        verifier = self._answer_verifier
        feedback: str | None = None
        attempts = 0
        # 空回复的补答额度：每次模型回合只补一次；补完仍空就是模型失败，不是"完成"。
        empty_retried = False
        # 本次尝试是否已把正文外发给用户：content 为空但正文已流出时**不重试**（会重复吐字）。
        streamed = {"value": False}
        # Host-owned failures must not stream a model-authored cause to the user before
        # deterministic finalization; the model may still request a read-only check.
        inner_delta = None if _host_owned_failure(state) else state["on_delta"]

        async def track_delta(text: str) -> None:
            # 每个流式增量外发前先判一次取消/截止：`is_cancelled` 由 1s 轮询置位，但 provider
            # 的 `async for` 读流循环本身不看它（`_parse_stream` 只累积 token）。不在这个每块
            # 必经的咽喉点检查，长回答要等整段吐完、`streamer()` 返回后才在流后检查点生效——
            # 用户点了取消仍在持续输出（实测 CANCEL 紧贴 MODEL_CALL_COMPLETED）。
            self._ensure_runnable(state)
            streamed["value"] = True
            if inner_delta is not None:
                await inner_delta(text)

        # 仅在流式路径（inner_delta 存在）包装：不改变「是否流式」的判定——非流式调用由
        # `_complete_with_recovery` 返回后的 `_ensure_runnable`（681 行）兜底。
        attempt_state = {**state, "on_delta": track_delta} if inner_delta is not None else state
        while True:
            streamed["value"] = False
            outbound = tuple(state["messages"])
            if feedback is not None:
                outbound = (*outbound, ModelMessage(role=ModelRole.USER, content=feedback))
            response = await self._complete_with_recovery(
                attempt_state,
                ModelRequest(
                    model_id=state["model_id"],
                    messages=outbound,
                    tools=self._registry.model_tools(),
                    temperature=state["temperature"],
                    max_tokens=state["max_tokens"],
                    params=state["params"],
                    timeout_sec=self._request_timeout_sec(state),
                ),
                # 验证开启时先不向用户流式输出：未通过验证的候选不得出现在回答里。
                stream=verifier is None,
            )
            if not response.finish_reason:
                # 流里没有任何收尾信息（上游被切断）：残缺回答不得当成功落成 COMPLETED。**不重试**——
                # 增量已经外发给用户了，重试会把同一段话再吐一遍（2026-10-06 review）。
                raise RunnerModelError("model stream ended without a completion signal")
            # 调用返回时总预算已经耗尽 ⇒ 就此收手（ADR-07 的 deadline 是硬预算）。检查点放在**调用
            # 之后、登记响应之前**：`_finalize` 阶段再判就成了"答案已经产出来还判它失败"，而
            # `_invoke_model` 里面的 provider 未必理会超时（stub / 自定义实现），不在这里兜一次，
            # 超预算的调用就会被当成功（2026-10-06 review）。
            self._ensure_runnable(state)
            if not response.tool_calls and not response.content.strip():
                # 空正文 + 无工具调用：既没有可见结果，也没有可继续的动作 —— 这不是"回答完了"。
                # 取消优先：被取消的 Run 必须以 CANCELLED 收口，不能报成"模型空回复"。
                cancelled = state["is_cancelled"]
                if cancelled is not None and cancelled():
                    raise RunnerCancelled("cancelled before an answer")
                if empty_retried or streamed["value"]:
                    raise RunnerEmptyReply("model returned an empty reply")
                empty_retried = True
                feedback = EMPTY_REPLY_NUDGE
                continue
            if verifier is None or response.tool_calls:
                return await self._accept_response(state, response)
            passed, reason = await verifier(response.content)
            attempts += 1
            if passed:
                return await self._accept_response(state, response)
            if attempts >= self._verification_max_attempts:
                raise RunnerVerificationExhausted("VERIFICATION_EXHAUSTED")
            feedback = (
                "上一个候选未通过验证：" + (reason or "未给出原因") + "。请修正后只输出最终答案。"
            )

    async def _accept_response(self, state: AgentRunState, response: ModelResponse) -> AgentRunState:
        calls = self._resolve_tool_calls(response.tool_calls)
        assistant = ModelMessage(
            role=ModelRole.ASSISTANT,
            content=response.content,
            tool_calls=response.tool_calls,
            reasoning_content=response.reasoning_content,
        )
        notify_assistant = state["on_assistant_turn"]
        if notify_assistant is not None:
            await notify_assistant(AssistantTurn(assistant, calls))
        return {
            **state,
            "messages": [*state["messages"], assistant],
            "pending_tool_calls": list(calls),
            "final_text": response.content,
            "assistant_tool_text": state["assistant_tool_text"] + (response.content if calls else ""),
            "turns": state["turns"] + 1,
            "input_tokens": _usage_sum(state["input_tokens"], response.input_tokens),
            "output_tokens": _usage_sum(state["output_tokens"], response.output_tokens),
            "consumed_event_seq": state["injected_event_seq"],
            "last_response": {
                "model_id": state["model_id"],
                "content": response.content,
                "tool_calls": list(response.tool_calls),
                "turns": state["turns"],
            },
            "post_model_pending": True,
        }

    async def _execute_tools(self, state: AgentRunState) -> AgentRunState:
        self._ensure_runnable(state)
        messages = list(state["messages"])
        runnable, exhausted_calls, used = self._reserve_tool_calls(state)
        state["tool_calls_used"] = used
        # 本回合真正执行过的调用：(消息下标, 调用 id, 工具名, 状态, outcome)。整批判定与
        # 完成事件都在回合末一次性收口（ADR-04）——不在循环里逐条报，否则产物 id 赶不上。
        executed: list[tuple[int, str, str, _CallOutcome]] = []
        unexecuted = [
            PendingToolCall(call_id=call.call_id, tool_name=call.name)
            for call in exhausted_calls
        ]
        for call in exhausted_calls:
            # 未执行不是失败：消息必须回给模型（合法的 tool 应答），但不进失败计数/完成事件。
            messages.append(_tool_message(call.call_id, TOOL_BUDGET_EXHAUSTED))
        # 补消息（`ToolResult.follow_ups`）**攒到回合末再落**，理由见 finally 里的注释。
        followed: list[ModelMessage] = []
        try:
            batches = plan_tool_batches(
                tuple(
                    PlannedToolCall(call.call_id, call.name, call.arguments)
                    for call in runnable
                ),
                registry=self._registry,
                parallel_limit=self._parallel_limit,
            )
            for batch in batches:
                # 取消/失约取消整批（TaskGroup 传播）；批内单调用失败只变成对应失败 ToolResult。
                self._ensure_runnable(state)
                if state["no_progress"] or state["window_limited"]:
                    self._append_not_executed(
                        messages, executed, batch,
                        reason=(
                            "tool call not executed after a platform rate limit"
                            if not state["no_progress"] else TOOL_NOT_EXECUTED
                        ),
                    )
                    continue
                outcomes = await self._run_batch(state, batch)
                for planned, outcome in zip(batch.calls, outcomes, strict=True):
                    messages.append(_tool_message(planned.call_id, outcome.content))
                    executed.append(
                        (
                            len(messages) - 1,
                            planned.call_id,
                            planned.tool_name,
                            outcome,
                        )
                    )
                    if (
                        outcome.status == "OK"
                        and planned.tool_name == "load_skill"
                        and isinstance(planned.arguments.get("skill_key"), str)
                    ):
                        state["active_skills"].add(str(planned.arguments["skill_key"]))
                    self._track_failure(state, planned.tool_name, outcome)
                    followed.extend(outcome.follow_ups)
        finally:
            # 补消息**必须先于回合收口、且落在整回合之后**：它是 `user` 角色，若插在两条 tool
            # 结果之间，后面那条结果就与声明它的 assistant 隔开了 —— 压缩器的 `split_groups`
            # 只把「当前组声明过的」tool 消息收进组，隔开的那条会被当孤儿**整条丢掉**（同轮
            # 「看图 + 其他工具」因此稳定丢结果，2026-10-06 review）。回合末追加后，这一回合
            # 仍是「assistant(声明) + 它的全部 tool 结果」这个不可分单元。
            messages.extend(followed)
            # 回合收口**必须走 finally**：中途被取消/超时打断时，已经跑完的那些同样是"发生过的事"
            # ——此前审计是逐条写的，中断时那些行已经落了；不收口等于把它们抹掉。
            await self._finish_tool_round(state, messages, executed, unexecuted)
        await self._emit_post_model(state)
        complete = {
            **state,
            "messages": messages,
            "pending_tool_calls": [],
            "unexecuted_tool_calls": [*state["unexecuted_tool_calls"], *unexecuted],
            "budget_exhausted": state["budget_exhausted"] or bool(exhausted_calls),
        }
        return await self._checkpoint_tool_round(cast(AgentRunState, complete))

    async def _checkpoint_tool_round(self, state: AgentRunState) -> AgentRunState:
        """完整回合边界：唯一的挂起点（Human 与工具级都走这里，差别只在 adapter 的请求）。

        挂起语义全在中立字段上：`kind` 决定收口状态（等人回答 / 等外部操作），`reason` 只是
        原样写进中断事件的宿主分类。Kernel 不解释任何一个——把 `reason` 当 Memory 是旧的错误形状。
        """
        port = self._tool_suspension
        request = port.suspend_request if port is not None else None
        if port is not None and request is not None and not state["no_progress"]:
            self._ensure_runnable(state)
            await port.suspend(self._checkpoint(state, request))
            await self.notify_interrupt(
                {
                    "interrupt_id": str(request.interrupt_id),
                    "kind": request.reason,
                    "prompt": request.prompt,
                }
            )
            return {**state, "status": str(_suspension_status(request)), "final_text": request.prompt}
        if self._runtime_events is not None and not state["budget_exhausted"]:
            await self._runtime_events.checkpoint(self._checkpoint(state))
        return state

    def _resolve_tool_calls(self, calls: Sequence[ModelToolCall]) -> tuple[ToolCall, ...]:
        """Map provider-safe names back to registry names before planning/execution."""
        resolved: list[ToolCall] = []
        for call in calls:
            try:
                definition = self._registry.get_by_model_name(call.name)
            except ToolNotFoundError:
                name = call.name
            else:
                name = definition.name
            resolved.append(
                ToolCall(
                    call_id=call.id,
                    name=name,
                    model_name=call.name,
                    arguments=call.arguments,
                )
            )
        return tuple(resolved)

    def _reserve_tool_calls(
        self, state: AgentRunState
    ) -> tuple[list[ToolCall], list[ToolCall], int]:
        """按原调用序预留 tool-call 预算；超出预算的调用转成明确失败消息、不再执行。"""
        runnable: list[ToolCall] = []
        exhausted: list[ToolCall] = []
        used = state["tool_calls_used"]
        for call in state["pending_tool_calls"]:
            if used >= state["policy"].max_tool_calls:
                exhausted.append(call)
                continue
            runnable.append(call)
            used += 1
        return runnable, exhausted, used

    async def _run_batch(self, state: AgentRunState, batch: ToolBatch) -> list[_CallOutcome]:
        """先按原序校验/授权，再把 handler IO 放进 TaskGroup（仅 lookup 与授权串行）。"""
        prepared = [await self._prepare_call(state, call) for call in batch.calls]
        if not batch.parallel:
            return [await self._run_prepared(state, item) for item in prepared]
        return await self._run_concurrently(state, prepared)

    async def _run_concurrently(
        self, state: AgentRunState, prepared: list[RunnableToolCall | _CallOutcome]
    ) -> list[_CallOutcome]:
        outcomes: list[_CallOutcome | None] = [None] * len(prepared)
        semaphore = asyncio.Semaphore(self._parallel_limit)

        async def run(index: int, item: RunnableToolCall | _CallOutcome) -> None:
            async with semaphore:
                outcomes[index] = await self._run_prepared(state, item)

        async with asyncio.TaskGroup() as group:
            for index, item in enumerate(prepared):
                group.create_task(run(index, item))
        return [cast(_CallOutcome, outcome) for outcome in outcomes]

    async def _prepare_call(
        self, state: AgentRunState, call: PlannedToolCall
    ) -> RunnableToolCall | _CallOutcome:
        """校验/授权（不含 handler IO）：失败转成对应失败 ToolResult，不撤销其他成功读。"""
        try:
            prepared = self._tool_pipeline.prepare(
                call_id=call.call_id, tool_name=call.tool_name, arguments=call.arguments
            )
            outcome = await self._tool_pipeline.prepare_execution(
                prepared, gate=self._dispatch_gate(state)
            )
        except Exception as exc:
            return self._exception_outcome(
                ToolCall(
                    call_id=call.call_id,
                    name=call.tool_name,
                    model_name=call.tool_name,
                    arguments=call.arguments,
                ),
                exc,
            )
        if isinstance(outcome, ToolExecutionResult):
            try:
                definition = self._registry.get(outcome.call.name)
            except ToolNotFoundError:
                rejected = None
            else:
                arguments = dict(outcome.call.arguments)
                rejected = build_prepared_call(replace(
                    prepared,
                    arguments=arguments,
                    args_hash=compute_args_hash(arguments),
                ), replace(definition, write_operation_key=None))
            return _CallOutcome(
                outcome.call,
                _TOOL_STATUSES.get(outcome.status, "ERROR"),
                outcome.result,
                rejected,
            )
        return outcome

    def _dispatch_gate(self, state: AgentRunState) -> Callable[
        [PreparedToolCall, ToolDefinition], ToolExecutionResult | None
    ]:
        """Single pre-policy gate for blocked attempts and unverified writes."""
        failure_state = _failure_state_of(state)

        def gate(prepared: PreparedToolCall, definition: ToolDefinition) -> ToolExecutionResult | None:
            call = build_prepared_call(prepared, definition)
            attempt = _attempt_of(call)
            effectful = is_effectful(call.effect, call.retry_safety.value)
            blocked = attempt in failure_state.blocked_attempts
            unknown_sources = (
                unverified_write_sources(
                    failure_state,
                    call.write_targets,
                    call.write_operation_key,
                    fallback_target=f"tool:{call.tool_name or call.call.name}",
                    retry_safety=call.retry_safety.value,
                )
                if effectful
                else None
            )
            unknown = unknown_sources is not None
            last_failure = failure_state.last_failure
            scope = failure_scope(
                call.call.name, skill_key=call.skill_key, skill_id=call.skill_id
            )
            host_owned_interaction = bool(
                failure_state.failure_pending
                and last_failure is not None
                and last_failure.action_owner in {"ADMIN", "NONE"}
                and call.call.name == "ask_user"
            )
            same_operation = bool(
                last_failure is not None
                and (
                    scope == last_failure.tool
                    or (
                        call.write_operation_key is not None
                        and call.write_operation_key == last_failure.write_operation_key
                    )
                    or bool(call.write_targets & last_failure.write_targets)
                )
            )
            unresolved_other = bool(
                failure_state.failure_pending
                and effectful
                and last_failure is not None
                and not same_operation
            )
            if not blocked and not unknown and not unresolved_other and not host_owned_interaction:
                return None
            blocked_source = failure_state.blocked_attempt_sources.get(attempt, "")
            unknown_source = (
                next(iter(unknown_sources))
                if unknown_sources is not None and len(unknown_sources) == 1
                else ""
            )
            if blocked and unknown:
                # Two safety reasons hold. They still share one root cause when both
                # point at the same earlier call; otherwise the relation is ambiguous.
                reason = RETRY_GATE_GUARDED
                source = blocked_source if blocked_source == unknown_source else ""
            elif blocked:
                reason = RETRY_GATE_BLOCKED_ATTEMPT
                source = blocked_source
            elif unknown:
                reason = RETRY_GATE_UNVERIFIED_WRITE
                source = unknown_source
            else:
                reason = "FAILURE_UNRESOLVED"
                source = last_failure.tool_call_id if last_failure is not None else ""
            detail: dict[str, object] = {"gate_reason": reason}
            if source:
                detail["source_tool_call_id"] = source
            if reason == "FAILURE_UNRESOLVED" and last_failure is not None:
                detail["failure_code"] = last_failure.code
            diagnostic: dict[str, object] = {
                "gate_reason": reason,
                "trigger_tool_call_id": call.call.call_id,
                "source_tool_call_id": source or None,
                "source_attempt": attempt if blocked else None,
                "write_operation_key": call.write_operation_key if effectful else None,
                "write_targets": sorted(call.write_targets) if effectful else [],
                # A safety gate is host-owned; the model must not narrate a guessed cause.
                "action_owner": "NONE",
                "hint_known": True,
            }
            return failure_result(
                call.call,
                ToolError(
                    "TOOL_RETRY_UNSAFE",
                    "The previous operation cannot safely be run again before its result is verified.",
                    detail=detail,
                    retryable=False,
                    diagnostic=diagnostic,
                ),
                status="BLOCKED",
            )

        return gate

    async def _run_prepared(
        self, state: AgentRunState, item: RunnableToolCall | _CallOutcome
    ) -> _CallOutcome:
        if isinstance(item, _CallOutcome):
            return item
        prepared = build_prepared_call(item.prepared, item.definition)
        effectful = is_effectful(prepared.effect, prepared.retry_safety.value)
        notify_prepared = state["on_tool_prepared"]
        if notify_prepared is not None:
            try:
                await notify_prepared(prepared)
            except Exception as exc:
                # 没有持久 prepared 事实就不得发生外部执行。
                return self._exception_outcome(prepared.call, exc)
        outcome = await self._invoke_prepared(item, prepared)
        if (not effectful or outcome.outcome.control.outcome_known) and await self._wait_for_tool_retry(
            state, outcome.outcome
        ):
            state["tool_calls_used"] += 1
            return await self._invoke_prepared(item, prepared)
        return outcome

    async def _invoke_prepared(self, item: RunnableToolCall, prepared: PreparedCall) -> _CallOutcome:
        try:
            result = await self._tool_pipeline.run_prepared(item)
        except Exception as exc:
            outcome = self._exception_outcome(
                prepared.call, exc,
                handler_entered=is_effectful(prepared.effect, prepared.retry_safety.value),
            )
            return replace(outcome, prepared=prepared, handler_entered=True)
        return _CallOutcome(
            result.call, _TOOL_STATUSES.get(result.status, "ERROR"), result.result, prepared,
            handler_entered=True,
        )

    async def _wait_for_tool_retry(self, state: AgentRunState, outcome: ExecutionOutcome) -> bool:
        if (
            outcome.control.agent_disposition is not AgentDisposition.WAIT
            or outcome.control.retry_disposition is not RetryDisposition.WAIT_THEN_RETRY
            or not outcome.retryable
            or not outcome.control.stop_confirmed
            or state["tool_calls_used"] >= state["policy"].max_tool_calls
        ):
            return False
        self._ensure_runnable(state)
        hint = outcome.control.retry_after
        retry_after = _retry_after_seconds(hint)
        if hint is not None and retry_after is None:
            return False
        budget = self._budget(state)
        delay = budget.retry_delay_sec(0, retry_after)
        elapsed_ms = (time.monotonic() - state["started_at"]) * 1000
        deadline = state["deadline_at"]
        if not budget.fits_before_deadline(elapsed_ms=elapsed_ms, delay_sec=delay):
            return False
        if deadline is not None and delay >= (deadline - datetime.now(UTC)).total_seconds():
            return False
        await self._cancel_aware_sleep(state, delay)
        self._ensure_runnable(state)
        return state["tool_calls_used"] < state["policy"].max_tool_calls

    def _exception_outcome(
        self, call: ToolCall, exc: BaseException, *, handler_entered: bool = False,
    ) -> _CallOutcome:
        outcome = _exception_outcome(exc, handler_entered=handler_entered)
        return _CallOutcome(
            call,
            "ERROR",
            ToolResult(
                content=tool_failure_content(exc),
                outcome=outcome,
                call_id=call.call_id,
            ),
        )

    def _append_not_executed(
        self,
        messages: list[ModelMessage],
        executed: list[tuple[int, str, str, _CallOutcome]],
        batch: ToolBatch,
        *,
        reason: str,
    ) -> None:
        """Every declared call gets a result, including calls skipped after an early stop."""
        for planned in batch.calls:
            call = ToolCall(
                call_id=planned.call_id,
                name=planned.tool_name,
                model_name=planned.tool_name,
                arguments=planned.arguments,
            )
            outcome = tool_exception_outcome(
                code=TOOL_NOT_EXECUTED_CODE,
                message=reason,
                retryable=False,
            )
            result = ToolResult(
                content=json.dumps(
                    {
                        "error": {
                            "code": TOOL_NOT_EXECUTED_CODE,
                            "message": reason,
                        }
                    },
                    ensure_ascii=False,
                ),
                outcome=outcome,
                call_id=call.call_id,
            )
            messages.append(_tool_message(planned.call_id, result.content))
            executed.append(
                (len(messages) - 1, planned.call_id, planned.tool_name, _CallOutcome(call, "ERROR", result))
            )

    def _track_failure(self, state: AgentRunState, tool_name: str, outcome: _CallOutcome) -> None:
        """Apply the shared failure transition; live execution and recovery must agree."""
        execution = outcome.outcome
        if execution is None:
            if outcome.status == "ERROR":
                execution = _exception_outcome(RuntimeError(outcome.content))
            else:
                self._apply_failure(state, None)
                return
        if (
            execution.business_status is BusinessStatus.RATE_LIMITED
            and state["window_limit_reply"] is not None
            and execution.control.retry_disposition.value != "WAIT_THEN_RETRY"
        ):
            state["window_limited"] = True
            return
        prepared = outcome.prepared
        scope = failure_scope(
            execution.tool_name or tool_name,
            skill_key=prepared.skill_key if prepared else None,
            skill_id=prepared.skill_id if prepared else None,
        )
        signal = FailureSignal(
            tool=scope,
            attempt=_attempt_of(prepared) if prepared else None,
            external_started=bool(
                outcome.handler_entered and prepared
                and is_effectful(prepared.effect, prepared.retry_safety.value)
            ),
            write_targets=prepared.write_targets if prepared else frozenset(),
            write_operation_key=prepared.write_operation_key if prepared else None,
            review_progress=execution.review_progress,
            tool_call_id=execution.call_id,
            source_call_id=_diagnostic_source(execution.evidence.diagnostic),
            action_owner=_diagnostic_owner(execution.evidence.diagnostic),
            hint_known=_diagnostic_hint_known(execution.evidence.diagnostic),
            business_status=str(execution.business_status),
            failure_layer=str(execution.failure_layer),
            error_code=execution.error_code,
            message=execution.error_message,
            exit_code=execution.exit_code,
            retryable=execution.retryable,
            agent_disposition=execution.control.agent_disposition.value,
            retry_disposition=execution.control.retry_disposition.value,
            scope=execution.control.scope.value,
            sent=execution.control.sent,
            outcome_known=execution.control.outcome_known,
            evidence_status=execution.control.evidence_status.value,
            retry_after=execution.control.retry_after,
            security_disposition=execution.control.security_disposition.value,
            boundary_kind=execution.control.boundary_kind.value,
            stop_confirmed=execution.control.stop_confirmed,
            evidence_preview=execution.evidence.preview,
            occurred_at=now_iso(),
        )
        self._apply_failure(state, signal)

    def _apply_failure(self, state: AgentRunState, signal: FailureSignal | None) -> None:
        updated = update_failure_state(
            _failure_state_of(state),
            signal,
            max_identical_failures=state["policy"].max_identical_failures,
        )
        state["last_failure"] = updated.last_failure
        state["failure_pending"] = updated.failure_pending
        state["identical_failures"] = dict(updated.identical_failures)
        state["blocked_attempts"] = updated.blocked_attempts
        state["blocked_attempt_sources"] = dict(updated.blocked_attempt_sources)
        state["unknown_write_targets"] = updated.unknown_write_targets
        state["unknown_target_sources"] = dict(updated.unknown_target_sources)
        state["unknown_write_operations"] = updated.unknown_write_operations
        state["unknown_operation_sources"] = dict(updated.unknown_operation_sources)
        state["reviews"] = dict(updated.reviews)
        state["no_progress"] = updated.no_progress
        state["no_progress_trigger_call_id"] = updated.no_progress_trigger_call_id
        state["no_progress_source_call_id"] = updated.no_progress_source_call_id
        self._latest_state = state

    async def _finish_tool_round(
        self,
        state: AgentRunState,
        messages: list[ModelMessage],
        executed: Sequence[tuple[int, str, str, _CallOutcome]],
        unexecuted: Sequence[PendingToolCall],
    ) -> None:
        """回合末收口：先让端口按**整轮**决定谁要外置（可改写结果），再逐条报完成。

        顺序是硬约束：`tool.completed` 上挂着产物 id，它进了 canonical 行、历史重建靠它把产物
        找回来，所以必须等整批判定落定之后再发。预算截断的调用也要报一条 `TOOL_BUDGET_EXHAUSTED`
        结果：canonical 回合必须始终是「assistant 声明 + 每个声明的 tool 结果」的完整单元，
        否则 checkpoint 的完整回合校验和后续历史重建都会看到孤儿声明。
        """
        if not executed:
            for call in unexecuted:
                await self._notify_tool_completed(
                    state,
                    call.call_id,
                    call.tool_name,
                    "ERROR",
                    TOOL_BUDGET_EXHAUSTED,
                    tool_exception_outcome(
                        code="TOOL_BUDGET_EXHAUSTED",
                        message=TOOL_BUDGET_EXHAUSTED,
                        retryable=False,
                    ),
                )
            return
        results = [item[3].result for item in executed]
        if self._round_results is not None:
            replaced = await self._round_results_or_inline(tuple(results))
            if replaced is not None:
                results = list(replaced)
        for (index, call_id, name, outcome), result in zip(
            executed, results, strict=True
        ):
            messages[index] = replace(messages[index], content=result.content)
            completed = replace(
                completed_from_result(outcome.call, result, outcome.status),
                handler_entered=outcome.handler_entered,
                attempt=_attempt_of(outcome.prepared) if outcome.prepared else None,
            )
            await self._notify_tool_completed(
                state,
                call_id,
                name,
                completed.status,
                completed.content,
                completed.outcome,
                completed=completed,
            )
        for call in unexecuted:
            await self._notify_tool_completed(
                state,
                call.call_id,
                call.tool_name,
                "ERROR",
                TOOL_BUDGET_EXHAUSTED,
                tool_exception_outcome(
                    code="TOOL_BUDGET_EXHAUSTED",
                    message=TOOL_BUDGET_EXHAUSTED,
                    retryable=False,
                ),
            )

    async def _round_results_or_inline(
        self, results: Sequence[ToolResult]
    ) -> Sequence[ToolResult] | None:
        """整轮外置失败 ⇒ 返回 `None`，调用方**保留内联结果**（整轮回滚，不得让 Run 失败）。

        Kernel 是这条保证的唯一落点：换第二个适配器时不必各自记得吞异常。降级必须可见
        （warning），不能变成"悄悄不外置"。
        """
        assert self._round_results is not None
        try:
            return await self._round_results.finish_round(results)
        except Exception as exc:  # noqa: BLE001 —— 外置是优化，失败不得改变主执行结果
            logger.warning(
                "tool_round_results_failed",
                extra={"results": len(results), "error_type": type(exc).__name__, "error": str(exc)},
            )
            return None

    async def _emit_post_model(self, state: AgentRunState) -> None:
        """设计 S-05 顺序：post_model 在 post_tool_use 之后、每次模型响应触发一次。"""
        if not state["post_model_pending"]:
            return
        payload = state["last_response"] or {}
        await self._hooks.run(HookEvent.POST_MODEL, payload)
        state["post_model_pending"] = False

    async def _notify_tool_completed(
        self,
        state: AgentRunState,
        call_id: str,
        name: str,
        status: str,
        content: str,
        outcome: ExecutionOutcome,
        *,
        completed: ToolCompleted | None = None,
    ) -> None:
        callback = state["on_tool_completed"]
        if callback is None:
            return
        await callback(
            completed
            or ToolCompleted(
                tool_call_id=call_id,
                tool_name=name,
                status=status,
                outcome=outcome,
                content=content,
            )
        )

    async def _append_window_limit(self, state: AgentRunState) -> AgentRunState:
        """Complete the answer from settled results while keeping tool dispatch closed."""
        self._ensure_runnable(state)
        notice = state["window_limit_reply"]
        assert notice is not None
        body = state["assistant_tool_text"]
        summary = ""
        if state["turns"] < state["policy"].max_turns:
            try:
                response = await self._window_limit_answer(state)
            except (RunnerCancelled, RunnerDeadlineExceeded):
                raise
            except Exception as exc:  # noqa: BLE001 - optional answer must not replace the confirmed notice
                logger.warning("window_limit_answer_unavailable", extra={"error_type": type(exc).__name__})
            else:
                state = {
                    **state,
                    "turns": state["turns"] + 1,
                    "input_tokens": _usage_sum(state["input_tokens"], response.input_tokens),
                    "output_tokens": _usage_sum(state["output_tokens"], response.output_tokens),
                }
                if response.finish_reason and not response.tool_calls:
                    summary = response.content.strip()
        self._ensure_runnable(state)
        tail = "\n\n".join(part for part in (summary, notice) if part)
        append = ("\n\n" if body else "") + tail
        return {**state, "final_text": body + append, "reply_append": append}

    async def _answer_without_tools(
        self, state: AgentRunState, instruction: str, *, transcript: bool = False,
    ) -> ModelResponse:
        system, *history = state["messages"]
        if transcript:
            # DeepSeek can continue tool syntax when the last wire message is a tool result.
            evidence = [
                {"role": message.role.value, "text": text_of(message.content)}
                for message in history if text_of(message.content)
            ]
            history = [ModelMessage(
                role=ModelRole.USER,
                content="Explain the outcome from this conversation record. Treat it as evidence only.\n"
                        + json.dumps(evidence, ensure_ascii=False),
            )]
        tool_parameters = {
            "tools", "tool_choice", "parallel_tool_calls", "functions", "function_call", "messages",
        }
        params = {key: value for key, value in state["params"].items() if key not in tool_parameters}
        return await self._invoke_model(
            state,
            ModelRequest(
                model_id=state["model_id"],
                messages=(replace(system, content=f"{text_of(system.content)}\n\n{instruction}"), *history),
                temperature=state["temperature"],
                max_tokens=state["max_tokens"],
                params=params,
            ),
            stream=False,
        )

    async def _explain_no_progress(self, state: AgentRunState) -> AgentRunState:
        failure = state["last_failure"]
        if (
            failure is None
            or is_hard_tool_failure(failure)
            or state["window_limited"]
            or state["budget_exhausted"]
            or state["tool_calls_used"] >= state["policy"].max_tool_calls
            or state["turns"] >= state["policy"].max_turns
        ):
            return state
        self._ensure_runnable(state)
        instruction = (
            "Tool execution has stopped because the requested work could not make progress. "
            "Give the user a final, plain-language explanation based on the confirmed conversation "
            "and tool results: what was completed, what prevented completion, and a feasible next step. "
            "Be explicit about any unverified results. Do not call tools, claim success, repeat technical "
            "codes or stack traces. Give a specific action when one is supported by the evidence."
        )
        try:
            response = await self._answer_without_tools(state, instruction, transcript=True)
        except (RunnerCancelled, RunnerDeadlineExceeded):
            raise
        except Exception as exc:  # noqa: BLE001 - a failed explanation leaves the host fallback available
            logger.warning("tool_failure_explanation_unavailable", extra={"error_type": type(exc).__name__})
            return state
        self._ensure_runnable(state)
        description = (
            response.content.strip() if response.finish_reason == "stop" and not response.tool_calls else ""
        )
        tool_markup = ("<｜｜dsml｜｜", "<||dsml||", "<tool_call", "<function_call")
        if any(marker in description.casefold() for marker in tool_markup):
            logger.warning("tool_failure_explanation_contains_tool_syntax")
            description = ""
        return {
            **state,
            "turns": state["turns"] + 1,
            "input_tokens": _usage_sum(state["input_tokens"], response.input_tokens),
            "output_tokens": _usage_sum(state["output_tokens"], response.output_tokens),
            "reply_append": description,
        }

    async def _window_limit_answer(self, state: AgentRunState) -> ModelResponse:
        instruction = (
            "A platform query was stopped by a frequency limit. Answer the user's request from the "
            "available, confirmed results. State which requested data is still unavailable; never "
            "invent missing results. Do not call tools. The platform will add the rate-limit notice."
        )
        return await self._answer_without_tools(state, instruction)

    async def _finalize(self, state: AgentRunState) -> AgentRunState:
        await self._emit_post_model(state)
        suspended = state["status"] in _SUSPENDED_STATUSES
        exceeded = not suspended and (
            state["budget_exhausted"]
            or (bool(state["pending_tool_calls"]) and state["turns"] >= state["policy"].max_turns)
        )
        status: AgentRunStatus
        if not suspended and _host_owned_failure(state):
            # Host-owned failure: the model must not turn it into a completed answer.
            status = AgentRunStatus.TOOL_FAILURE
        elif not suspended and (
            state["no_progress"] or (state["window_limited"] and state["last_failure"] is not None)
        ):
            status = AgentRunStatus.NO_PROGRESS
        elif exceeded:
            status = AgentRunStatus.BUDGET_EXCEEDED
        else:
            status = AgentRunStatus(state["status"])
        if state["window_limited"] and status == AgentRunStatus.COMPLETED:
            state = await self._append_window_limit(state)
        if status == AgentRunStatus.NO_PROGRESS:
            state = await self._explain_no_progress(state)
        text = state["final_text"]
        self._stop_emitted = True
        context = await self._hooks.run(HookEvent.STOP, {"final_text": text, "turns": state["turns"]})
        final_text = context.payload.get("final_text")
        return {**state, "final_text": final_text if isinstance(final_text, str) else text, "status": status}

    def _snapshot(self, state: AgentRunState) -> TerminationSnapshot:
        """把终止事实冻结成 Runtime 可消费的形状；budget subtype 从计数器推导。"""
        policy = state["policy"]
        suspended = state["status"] in _SUSPENDED_STATUSES
        if suspended or state["no_progress"] or (
            state["window_limited"] and state["status"] == AgentRunStatus.COMPLETED
        ):
            budget_kind = BudgetKind.NONE
        elif state["turns"] >= policy.max_turns:
            budget_kind = BudgetKind.TURN
        elif state["tool_calls_used"] >= policy.max_tool_calls or state["budget_exhausted"]:
            budget_kind = BudgetKind.TOOL
        else:
            budget_kind = BudgetKind.NONE
        pending = list(state["unexecuted_tool_calls"])
        seen = {call.call_id for call in pending}
        for call in state["pending_tool_calls"]:
            if call.call_id not in seen:
                pending.append(
                    PendingToolCall(call_id=call.call_id, tool_name=call.name)
                )
                seen.add(call.call_id)
        return TerminationSnapshot(
            status=state["status"],
            budget_kind=budget_kind,
            turns_used=state["turns"],
            turns_limit=policy.max_turns,
            tool_calls_used=state["tool_calls_used"],
            tool_calls_limit=policy.max_tool_calls,
            elapsed_ms=int((time.monotonic() - state["started_at"]) * 1000),
            deadline_ms=policy.deadline_ms,
            failure_state=_failure_state_of(state),
            pending_tool_calls=tuple(pending),
        )

    def _route_after_model(self, state: AgentRunState) -> str:
        if state["no_progress"]:
            return NODE_FINALIZE
        if not state["pending_tool_calls"]:
            return NODE_DECIDE if self._runtime_events is not None else NODE_FINALIZE
        if state["turns"] >= state["policy"].max_turns:
            # 轮次打满时不再执行这批工具；未执行调用只进 snapshot 的 pending。
            return NODE_FINALIZE
        return NODE_TOOLS

    async def _decide_after_model(self, state: AgentRunState) -> AgentRunState:
        port = self._runtime_events
        if port is None:
            return {**state, "next_node": NODE_FINALIZE}
        await port.checkpoint(self._checkpoint(state))
        state = await self._drain_events(state)
        if state["injected_event_seq"] > state["consumed_event_seq"]:
            return {**state, "next_node": NODE_PREPARE_CONTEXT}
        decision = await port.try_wait(self._checkpoint(state), assistant_text=state["final_text"])
        if decision == WaitDecision.CONTINUE:
            return {**state, "next_node": NODE_PREPARE_CONTEXT}
        status = AgentRunStatus.WAITING_TOOL if decision == WaitDecision.WAIT else AgentRunStatus.COMPLETED
        return {**state, "status": str(status), "next_node": NODE_FINALIZE}

    @staticmethod
    def _route_after_prepare(state: AgentRunState) -> str:
        return (
            NODE_FINALIZE if state["budget_exhausted"] or state["no_progress"] else NODE_MODEL
        )

    def _checkpoint(
        self,
        state: AgentRunState, suspension: SuspensionRequest | None = None
    ) -> RunnerCheckpoint:
        """回合 checkpoint；工具级挂起时把待定操作一起写进去（Human 挂起恒为空）。

        checkpoint 是恢复的唯一依据：模型回合边界与异步工具挂起**同一形状**，宿主换一份
        `CheckpointStore` 就能恢复，不需要第二套私有约定。失败状态可选随 checkpoint 持久化，
        旧 checkpoint 缺失时按空状态恢复，不臆造失败。
        """
        failure_state = _failure_state_of(state)
        return RunnerCheckpoint(
            state["turns"],
            state["tool_calls_used"],
            state["input_tokens"],
            state["output_tokens"],
            state["consumed_event_seq"],
            suspension.pending_tool_calls if suspension is not None else (),
            failure_state=failure_state.as_payload(),
        )

    async def _drain_events(self, state: AgentRunState) -> AgentRunState:
        if self._runtime_events is None:
            return state
        messages, cursor = list(state["messages"]), state["injected_event_seq"]
        while True:
            batch = await self._runtime_events.drain_ready(cursor, self._event_batch_size)
            messages.extend(batch.messages)
            cursor = batch.cursor
            if not batch.has_more:
                break
            self._ensure_runnable(state)
        return {**state, "messages": messages, "injected_event_seq": cursor}

    @staticmethod
    def _route_after_tools(state: AgentRunState) -> str:
        if (
            state["budget_exhausted"] or state["no_progress"] or state["window_limited"]
            or state["status"] in _SUSPENDED_STATUSES
        ):
            # 两类挂起（等人 / 等外部操作）都在这里收口：继续跑模型回合等于把挂起当没发生。
            return NODE_FINALIZE
        return NODE_PREPARE_CONTEXT

    def _ensure_runnable(self, state: AgentRunState) -> None:
        check = state["is_cancelled"]
        if check is not None and check():
            raise RunnerCancelled("run cancelled")
        elapsed_ms = (time.monotonic() - state["started_at"]) * 1000
        deadline = state["deadline_at"]
        if elapsed_ms >= state["policy"].deadline_ms or (
            deadline is not None and datetime.now(UTC) >= deadline
        ):
            raise RunnerDeadlineExceeded(f"deadline exceeded: {state['policy'].deadline_ms}ms")

    async def _complete_with_recovery(
        self,
        state: AgentRunState,
        request: ModelRequest,
        *,
        stream: bool = True,
    ) -> ModelResponse:
        attempts = 0
        while True:
            self._ensure_runnable(state)
            try:
                return await self._invoke_model(state, request, stream=stream)
            except ModelRateLimitedError as exc:
                await self._cancel_aware_sleep(state, self._retry_delay(state, attempts, exc.retry_after))
                attempts += 1
            except ModelUnavailableError:
                await self._cancel_aware_sleep(state, self._retry_delay(state, attempts, None))
                attempts += 1
            except ModelRequestError as exc:
                raise RunnerModelError(f"model request rejected: {exc}") from exc

    async def _invoke_model(
        self, state: AgentRunState, request: ModelRequest, *, stream: bool = True
    ) -> ModelResponse:
        request = await self._prepare_model_request(state, request)
        started, completed = state["on_model_started"], state["on_model_completed"]
        if started is not None:
            await started()
        try:
            streamer = getattr(self._provider, "stream", None)
            on_delta = state["on_delta"]
            if stream and on_delta is not None and streamer is not None:
                return cast(ModelResponse, await streamer(request, on_delta))
            return await self._provider.complete(request)
        finally:
            if completed is not None:
                await completed()

    async def _prepare_model_request(self, state: AgentRunState, request: ModelRequest) -> ModelRequest:
        messages = request.messages
        if self._context_loader is not None:
            if self._startup_component_pending:
                component = self._startup_component
                self._startup_component_pending = False
            else:
                component = await self._context_loader.refresh()
            message = component.message if component is not None else None
            if message is not None:
                messages = (*messages[:1], message, *messages[1:])
        if self._context_compactor is not None:
            messages = await self._compacted(state, messages)
        self._ensure_runnable(state)
        return replace(request, messages=messages, timeout_sec=self._request_timeout_sec(state))

    async def _compacted(
        self, state: AgentRunState, messages: Sequence[ModelMessage]
    ) -> tuple[ModelMessage, ...]:
        """压缩失败一律退回原历史（"压缩不得让 Run 失败"），但降级必须可见。

        Kernel 兜这一层是因为它有两个适配器：第二个实现不该靠"记得吞异常"来复用同一条规则。
        """
        assert self._context_compactor is not None
        try:
            plan = plan_context(
                messages,
                run_input=state["run_input"],
                active_skills=tuple(state["active_skills"]),
                covered_seq=state["covered_seq"],
                material_scan_end_seq=state["material_scan_end_seq"],
                head_end_seq=state["head_end_seq"],
                earlier_records=state["earlier_records"],
            )
            compacted = await self._context_compactor.compact_plan(plan)
            return render_context(compacted)
        except Exception as exc:  # noqa: BLE001 —— 压缩是优化，失败不得改变主执行
            logger.warning(
                "context_compaction_failed",
                extra={"turns": state["turns"], "error_type": type(exc).__name__, "error": str(exc)},
            )
            return tuple(messages)

    async def _cancel_aware_sleep(self, state: AgentRunState, delay: float) -> None:
        """退避期间保持 cancel/deadline 可响应。"""
        remaining = delay
        while remaining > 0:
            self._ensure_runnable(state)
            step = min(remaining, 0.1)
            await asyncio.sleep(step)
            remaining -= step

    def _budget(self, state: AgentRunState) -> ModelBudget:
        policy = state["policy"]
        return ModelBudget(deadline_ms=policy.deadline_ms, max_model_retries=policy.max_model_retries)

    def _request_timeout_sec(self, state: AgentRunState) -> float:
        """这一次模型请求的 I/O 超时 = min(单次上限, **剩余**总预算)（ADR-07）。

        不夹到剩余预算，总 deadline 就约束不到正在飞的那次调用：单次调用可以一路跑到（甚至跑过）
        整个 deadline 而没有任何一层拦它（2026-10-06 review）。
        """
        elapsed_ms = (time.monotonic() - state["started_at"]) * 1000
        timeout = self._budget(state).request_timeout_sec(elapsed_ms)
        deadline = state["deadline_at"]
        return (
            min(timeout, max(0.001, (deadline - datetime.now(UTC)).total_seconds())) if deadline else timeout
        )

    def _retry_delay(
        self,
        state: AgentRunState,
        attempt: int,
        retry_after: float | None,
    ) -> float:
        budget = self._budget(state)
        if attempt >= budget.max_model_retries:
            raise RunnerModelError(f"model retries exhausted: {attempt}")
        delay = budget.retry_delay_sec(attempt, retry_after)
        elapsed_ms = (time.monotonic() - state["started_at"]) * 1000
        if not budget.fits_before_deadline(elapsed_ms=elapsed_ms, delay_sec=delay):
            raise RunnerDeadlineExceeded("retry would exceed the run deadline")
        return delay
