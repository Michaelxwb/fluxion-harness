from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..context.compactor import ContextCompactor
from ..hooks.pipeline import HookEvent, HookPipeline
from ..model.budget import (
    DEFAULT_DEADLINE_MS,
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
from ..tools.pipeline import ToolExecutionPipeline
from ..tools.registry import ToolRegistry
from ..tools.round_results import ToolResultRoundPort
from .continuation import RunnerCheckpoint, RuntimeEventPort, WaitDecision

NODE_PREPARE_CONTEXT = "prepare_context"
NODE_MODEL = "model"
NODE_TOOLS = "tools"
NODE_FINALIZE = "finalize"
NODE_DECIDE = "decide"
TOOL_BUDGET_EXHAUSTED = "tool call budget exhausted"
UNKNOWN_TOOL_TEMPLATE = "unknown tool: {name}"
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
        return json.dumps({"error": {"code": code, "message": str(message)}}, ensure_ascii=False)
    return TOOL_FAILED_TEMPLATE.format(reason=f"{type(exc).__name__}: {exc}")


class RunnerError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class RunnerModelError(RunnerError):
    pass


class RunnerDeadlineExceeded(RunnerError):
    pass


class RunnerCancelled(RunnerError):
    pass


class AgentRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_INPUT = "WAITING_INPUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    max_turns: int = DEFAULT_MAX_TURNS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    deadline_ms: int = DEFAULT_DEADLINE_MS
    max_model_retries: int = DEFAULT_MAX_MODEL_RETRIES

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
        )


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    model_id: str
    instructions: str
    messages: tuple[ModelMessage, ...] = ()
    skills: tuple[PromptSkill, ...] = ()
    policy: AgentPolicy = AgentPolicy()
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


@dataclass(frozen=True, slots=True)
class RunnerCallbacks:
    is_cancelled: Callable[[], bool] | None = None
    on_delta: DeltaCallback | None = None
    on_tool_started: Callable[[str, str], Awaitable[None]] | None = None
    on_model_started: Callable[[], Awaitable[None]] | None = None
    on_model_completed: Callable[[], Awaitable[None]] | None = None
    on_assistant_turn: Callable[[ModelMessage], Awaitable[None]] | None = None
    on_tool_completed: Callable[[str, str, str, str | None, str | None], Awaitable[None]] | None = None


class AgentGraphState(TypedDict):
    model_id: str
    instructions: str
    skills: list[PromptSkill]
    policy: AgentPolicy
    temperature: float | None
    max_tokens: int | None
    params: dict[str, Any]
    messages: list[ModelMessage]
    pending_tool_calls: list[ModelToolCall]
    final_text: str
    turns: int
    tool_calls_used: int
    input_tokens: int | None
    output_tokens: int | None
    budget_exhausted: bool
    status: str
    started_at: float
    is_cancelled: Callable[[], bool] | None
    on_delta: DeltaCallback | None
    on_tool_started: Callable[[str, str], Awaitable[None]] | None
    on_model_started: Callable[[], Awaitable[None]] | None
    on_model_completed: Callable[[], Awaitable[None]] | None
    # 每次模型响应触发一次，带完整 assistant 消息（含 tool_calls 与 reasoning_content）：
    # 调用方据此把「一个 assistant 回合」原样持久化，供后续 Run 重建**合法**历史。
    on_assistant_turn: Callable[[ModelMessage], Awaitable[None]] | None
    #: `(call_id, 工具名, 状态, 产物 id, 未外置时的那条结果正文)`。最后一个参数只在结果
    #: **没有被外置**时给出——那种结果没有产物可指，正文是它唯一能被后续 Run 找回来的载体。
    on_tool_completed: Callable[[str, str, str, str | None, str | None], Awaitable[None]] | None
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
        runtime_events: RuntimeEventPort | None = None,
        event_batch_size: int = 16,
    ) -> None:
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
        self._event_batch_size = event_batch_size
        self._stop_emitted = False
        self._graph = self._build_graph()

    async def run(
        self,
        request: AgentRunRequest,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        on_delta: DeltaCallback | None = None,
        on_tool_started: Callable[[str, str], Awaitable[None]] | None = None,
        on_model_started: Callable[[], Awaitable[None]] | None = None,
        on_model_completed: Callable[[], Awaitable[None]] | None = None,
        on_assistant_turn: Callable[[ModelMessage], Awaitable[None]] | None = None,
        on_tool_completed: Callable[[str, str, str, str | None, str | None], Awaitable[None]] | None = None,
        checkpoint: RunnerCheckpoint | None = None,
        context_cursor: int | None = None,
    ) -> AgentRunResult:
        callbacks = RunnerCallbacks(is_cancelled, on_delta, on_tool_started, on_model_started,
            on_model_completed, on_assistant_turn, on_tool_completed)
        initial = self._initial_state(request, checkpoint or RunnerCheckpoint(), callbacks, context_cursor)
        try:
            final = cast(AgentGraphState, await self._graph.ainvoke(initial,
                config={"recursion_limit": max(25, request.policy.max_turns * 4 + 4)}))
        except Exception:
            if not self._stop_emitted:
                self._stop_emitted = True
                await self._hooks.run(HookEvent.STOP, {"final_text": "", "turns": 0, "error": True})
            raise
        return AgentRunResult(final["final_text"], AgentRunStatus(final["status"]), final["turns"],
            final["tool_calls_used"],
            ModelUsage(input_tokens=final["input_tokens"], output_tokens=final["output_tokens"]))

    def _initial_state(self, request: AgentRunRequest, progress: RunnerCheckpoint, callbacks: RunnerCallbacks,
                       context_cursor: int | None) -> AgentGraphState:
        system = self._prompt_builder.build(instructions=request.instructions, skills=request.skills)
        return AgentGraphState(
            model_id=request.model_id,
            instructions=request.instructions,
            skills=list(request.skills),
            policy=request.policy,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            params=dict(request.params),
            messages=[ModelMessage(role=ModelRole.SYSTEM, content=system), *request.messages],
            pending_tool_calls=[],
            final_text="",
            turns=progress.turns,
            tool_calls_used=progress.tool_calls,
            input_tokens=progress.input_tokens,
            output_tokens=progress.output_tokens,
            budget_exhausted=False,
            status=str(AgentRunStatus.COMPLETED),
            started_at=time.monotonic(),
            is_cancelled=callbacks.is_cancelled,
            on_delta=callbacks.on_delta,
            on_tool_started=callbacks.on_tool_started,
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

    def _build_graph(
        self,
    ) -> CompiledStateGraph[AgentGraphState, None, AgentGraphState, AgentGraphState]:
        builder: StateGraph[AgentGraphState, None, AgentGraphState, AgentGraphState] = StateGraph(
            AgentGraphState
        )
        builder.add_node(NODE_PREPARE_CONTEXT, self._prepare_context)
        builder.add_node(NODE_MODEL, self._call_model)
        builder.add_node(NODE_TOOLS, self._execute_tools)
        builder.add_node(NODE_FINALIZE, self._finalize)
        builder.add_node(NODE_DECIDE, self._decide_after_model)
        builder.add_edge(START, NODE_PREPARE_CONTEXT)
        builder.add_conditional_edges(
            NODE_PREPARE_CONTEXT,
            self._route_after_prepare,
            {NODE_MODEL: NODE_MODEL, NODE_FINALIZE: NODE_FINALIZE},
        )
        builder.add_conditional_edges(
            NODE_MODEL,
            self._route_after_model,
            {NODE_TOOLS: NODE_TOOLS, NODE_FINALIZE: NODE_FINALIZE, NODE_DECIDE: NODE_DECIDE},
        )
        builder.add_conditional_edges(
            NODE_DECIDE,
            lambda state: state["next_node"],
            {NODE_FINALIZE: NODE_FINALIZE, NODE_PREPARE_CONTEXT: NODE_PREPARE_CONTEXT},
        )
        builder.add_conditional_edges(
            NODE_TOOLS,
            self._route_after_tools,
            {NODE_PREPARE_CONTEXT: NODE_PREPARE_CONTEXT, NODE_FINALIZE: NODE_FINALIZE},
        )
        builder.add_edge(NODE_FINALIZE, END)
        return builder.compile()

    async def notify_interrupt(self, payload: dict[str, Any]) -> None:
        """澄清/确认暂停等 interrupt 事件：触发 on_interrupt Hook（可观测）。"""
        await self._hooks.run(HookEvent.ON_INTERRUPT, dict(payload))

    async def _prepare_context(self, state: AgentGraphState) -> AgentGraphState:
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

    async def _call_model(self, state: AgentGraphState) -> AgentGraphState:
        self._ensure_runnable(state)
        await self._hooks.run(
            HookEvent.PRE_MODEL,
            {"model_id": state["model_id"], "messages": state["messages"], "turns": state["turns"]},
        )
        outbound = tuple(state["messages"])
        if self._context_compactor is not None:
            outbound = await self._context_compactor.compact(outbound)
        response = await self._complete_with_recovery(
            state,
            ModelRequest(
                model_id=state["model_id"],
                messages=outbound,
                tools=self._registry.list(),
                temperature=state["temperature"],
                max_tokens=state["max_tokens"],
                params=state["params"],
                timeout_sec=self._request_timeout_sec(state),
            ),
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
        return await self._accept_response(state, response)

    async def _accept_response(self, state: AgentGraphState, response: ModelResponse) -> AgentGraphState:
        assistant = ModelMessage(
            role=ModelRole.ASSISTANT,
            content=response.content,
            tool_calls=response.tool_calls,
            reasoning_content=response.reasoning_content,
        )
        notify_assistant = state["on_assistant_turn"]
        if notify_assistant is not None:
            await notify_assistant(assistant)
        return {
            **state,
            "messages": [*state["messages"], assistant],
            "pending_tool_calls": list(response.tool_calls),
            "final_text": response.content,
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

    async def _execute_tools(self, state: AgentGraphState) -> AgentGraphState:
        self._ensure_runnable(state)
        messages = list(state["messages"])
        used = state["tool_calls_used"]
        exhausted = False
        # 本回合真正执行过的调用：(消息下标, 调用 id, 工具名, 状态)。整批判定与完成事件都在
        # 回合末一次性收口（ADR-04）——不在循环里逐条报，否则产物 id 赶不上那条事件。
        executed: list[tuple[int, str, str, str]] = []
        # 补消息（`ToolDefinition.follow_up_messages`）**攒到回合末再落**，理由见 finally 里的注释。
        followed: list[ModelMessage] = []
        try:
            for call in state["pending_tool_calls"]:
                self._ensure_runnable(state)
                if used >= state["policy"].max_tool_calls:
                    exhausted = True
                    messages.append(_tool_message(call.id, TOOL_BUDGET_EXHAUSTED))
                    continue
                result, status = await self._run_tool_call(state, call)
                messages.append(result)
                executed.append((len(messages) - 1, call.id, call.name, status))
                used += 1
                # 工具结果之后可能需要补消息（见 `ToolDefinition.follow_up_messages`）：
                # tool 角色不能携带图像块，「重看图片」只能落成一条 user 消息。
                try:
                    definition = self._registry.get(call.name)
                except LookupError:
                    definition = None
                if definition is not None and definition.follow_up_messages is not None:
                    followed.extend(await definition.follow_up_messages(text_of(result.content)))
        finally:
            # 补消息**必须先于回合收口、且落在整回合之后**：它是 `user` 角色，若插在两条 tool
            # 结果之间，后面那条结果就与声明它的 assistant 隔开了 —— 压缩器的 `split_groups`
            # 只把「当前组声明过的」tool 消息收进组，隔开的那条会被当孤儿**整条丢掉**（同轮
            # 「看图 + 其他工具」因此稳定丢结果，2026-10-06 review）。回合末追加后，这一回合
            # 仍是「assistant(声明) + 它的全部 tool 结果」这个不可分单元。
            messages.extend(followed)
            # 回合收口**必须走 finally**：中途被取消/超时打断时，已经跑完的那些同样是"发生过的事"
            # ——此前审计是逐条写的，中断时那些行已经落了；不收口等于把它们抹掉。
            await self._finish_tool_round(state, messages, executed)
        await self._emit_post_model(state)
        complete = {
            **state,
            "messages": messages,
            "pending_tool_calls": [],
            "tool_calls_used": used,
            "budget_exhausted": state["budget_exhausted"] or exhausted,
        }
        if self._runtime_events is not None and not exhausted:
            await self._runtime_events.checkpoint(self._checkpoint(cast(AgentGraphState, complete)))
        return cast(AgentGraphState, complete)

    async def _finish_tool_round(
        self,
        state: AgentGraphState,
        messages: list[ModelMessage],
        executed: Sequence[tuple[int, str, str, str]],
    ) -> None:
        """回合末收口：先让端口按**整轮**决定谁要外置（可改写消息内容），再逐条报完成。

        顺序是硬约束：`tool.completed` 上挂着产物 id，它进了 canonical 行、历史重建靠它把产物
        找回来，所以必须等整批判定落定之后再发。
        """
        if not executed:
            return
        if self._round_results is not None:
            indexes = [index for index, *_ in executed]
            replaced = await self._round_results.finish_round(tuple(messages[index] for index in indexes))
            for index, message in zip(indexes, replaced, strict=True):
                messages[index] = message
        for index, call_id, name, status in executed:
            content = text_of(messages[index].content)
            artifact_id = _artifact_ref(content)
            await self._notify_tool_completed(
                state,
                call_id,
                name,
                status,
                artifact_id,
                # 没外置 ⇒ 正文必须随事件带走：库里那条 canonical 行若只留工具名，下一 Run 重建
                # 历史时这条结果就**净丢了**（任务 id、查询结论、短正文都找不回来）。
                None if artifact_id is not None else content,
            )

    async def _emit_post_model(self, state: AgentGraphState) -> None:
        """设计 S-05 顺序：post_model 在 post_tool_use 之后、每次模型响应触发一次。"""
        if not state["post_model_pending"]:
            return
        payload = state["last_response"] or {}
        await self._hooks.run(HookEvent.POST_MODEL, payload)
        state["post_model_pending"] = False

    async def _run_tool_call(self, state: AgentGraphState, call: ModelToolCall) -> tuple[ModelMessage, str]:
        try:
            prepared = self._tool_pipeline.prepare(
                call_id=call.id, tool_name=call.name, arguments=call.arguments
            )
            result = await self._tool_pipeline.execute(
                prepared,
                on_started=state["on_tool_started"],
            )
        except Exception as exc:
            return _tool_message(call.id, tool_failure_content(exc)), "ERROR"
        statuses = {
            "SUCCEEDED": "OK",
            "FAILED": "ERROR",
            "POLICY_DENIED": "BLOCKED",
            "NOT_FOUND": "NOT_FOUND",
        }
        return _tool_message(call.id, result.content), statuses[result.status]

    async def _notify_tool_completed(
        self,
        state: AgentGraphState,
        call_id: str,
        name: str,
        status: str,
        artifact_id: str | None,
        result_text: str | None,
    ) -> None:
        callback = state["on_tool_completed"]
        if callback is not None:
            await callback(call_id, name, status, artifact_id, result_text)

    async def _finalize(self, state: AgentGraphState) -> AgentGraphState:
        await self._emit_post_model(state)
        self._stop_emitted = True
        context = await self._hooks.run(
            HookEvent.STOP,
            {"final_text": state["final_text"], "turns": state["turns"]},
        )
        final_text = context.payload.get("final_text", state["final_text"])
        exceeded = state["budget_exhausted"] or (
            bool(state["pending_tool_calls"]) and state["turns"] >= state["policy"].max_turns
        )
        return {
            **state,
            "final_text": final_text if isinstance(final_text, str) else state["final_text"],
            "status": str(AgentRunStatus.BUDGET_EXCEEDED) if exceeded else state["status"],
        }

    def _route_after_model(self, state: AgentGraphState) -> str:
        if not state["pending_tool_calls"]:
            return NODE_DECIDE if self._runtime_events is not None else NODE_FINALIZE
        if state["turns"] >= state["policy"].max_turns:
            return NODE_FINALIZE
        return NODE_TOOLS

    async def _decide_after_model(self, state: AgentGraphState) -> AgentGraphState:
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
    def _route_after_prepare(state: AgentGraphState) -> str:
        return NODE_FINALIZE if state["budget_exhausted"] else NODE_MODEL

    @staticmethod
    def _checkpoint(state: AgentGraphState) -> RunnerCheckpoint:
        return RunnerCheckpoint(
            state["turns"],
            state["tool_calls_used"],
            state["input_tokens"],
            state["output_tokens"],
            state["consumed_event_seq"],
        )

    async def _drain_events(self, state: AgentGraphState) -> AgentGraphState:
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
    def _route_after_tools(state: AgentGraphState) -> str:
        if state["budget_exhausted"]:
            return NODE_FINALIZE
        return NODE_PREPARE_CONTEXT

    def _ensure_runnable(self, state: AgentGraphState) -> None:
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
        state: AgentGraphState,
        request: ModelRequest,
    ) -> ModelResponse:
        attempts = 0
        while True:
            self._ensure_runnable(state)
            try:
                return await self._invoke_model(state, request)
            except ModelRateLimitedError as exc:
                await self._cancel_aware_sleep(state, self._retry_delay(state, attempts, exc.retry_after))
                attempts += 1
            except ModelUnavailableError:
                await self._cancel_aware_sleep(state, self._retry_delay(state, attempts, None))
                attempts += 1
            except ModelRequestError as exc:
                raise RunnerModelError(f"model request rejected: {exc}") from exc

    async def _invoke_model(self, state: AgentGraphState, request: ModelRequest) -> ModelResponse:
        started, completed = state["on_model_started"], state["on_model_completed"]
        if started is not None:
            await started()
        try:
            streamer = getattr(self._provider, "stream", None)
            on_delta = state["on_delta"]
            if on_delta is not None and streamer is not None:
                return cast(ModelResponse, await streamer(request, on_delta))
            return await self._provider.complete(request)
        finally:
            if completed is not None:
                await completed()

    async def _cancel_aware_sleep(self, state: AgentGraphState, delay: float) -> None:
        """退避期间保持 cancel/deadline 可响应。"""
        remaining = delay
        while remaining > 0:
            self._ensure_runnable(state)
            step = min(remaining, 0.1)
            await asyncio.sleep(step)
            remaining -= step

    def _budget(self, state: AgentGraphState) -> ModelBudget:
        policy = state["policy"]
        return ModelBudget(deadline_ms=policy.deadline_ms, max_model_retries=policy.max_model_retries)

    def _request_timeout_sec(self, state: AgentGraphState) -> float:
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
        state: AgentGraphState,
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
