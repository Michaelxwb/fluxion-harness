from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..hooks.pipeline import HookEvent, HookPipeline
from ..model.errors import (
    ModelRateLimitedError,
    ModelRequestError,
    ModelUnavailableError,
)
from ..model.provider import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelUsage,
)
from ..prompt.builder import DefaultPromptBuilder, PromptBuilder, PromptSkill
from ..tools.registry import ToolNotFoundError, ToolRegistry

NODE_PREPARE_CONTEXT = "prepare_context"
NODE_MODEL = "model"
NODE_TOOLS = "tools"
NODE_FINALIZE = "finalize"
DEFAULT_RETRY_BASE_SEC = 0.1
TOOL_BUDGET_EXHAUSTED = "tool call budget exhausted"
UNKNOWN_TOOL_TEMPLATE = "unknown tool: {name}"
TOOL_BLOCKED_TEMPLATE = "tool blocked before execution: {reason}"
TOOL_FAILED_TEMPLATE = "tool failed: {reason}"


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
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    max_turns: int = 20
    max_tool_calls: int = 30
    deadline_ms: int = 120_000
    max_model_retries: int = 3

    @classmethod
    def from_runtime_config(cls, config: Mapping[str, Any]) -> AgentPolicy:
        defaults = cls()
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


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    final_text: str
    status: AgentRunStatus
    turns: int
    tool_calls: int
    usage: ModelUsage


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
    input_tokens: int
    output_tokens: int
    budget_exhausted: bool
    status: str
    started_at: float
    is_cancelled: Callable[[], bool] | None


def _limit(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _tool_message(call_id: str, content: str) -> ModelMessage:
    return ModelMessage(role=ModelRole.TOOL, content=content, tool_call_id=call_id)


class AgentRunner:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        registry: ToolRegistry,
        hooks: HookPipeline | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._hooks = hooks or HookPipeline()
        self._prompt_builder = prompt_builder or DefaultPromptBuilder()
        self._graph = self._build_graph()

    async def run(
        self,
        request: AgentRunRequest,
        *,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AgentRunResult:
        system = self._prompt_builder.build(instructions=request.instructions, skills=request.skills)
        initial = AgentGraphState(
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
            turns=0,
            tool_calls_used=0,
            input_tokens=0,
            output_tokens=0,
            budget_exhausted=False,
            status=str(AgentRunStatus.COMPLETED),
            started_at=time.monotonic(),
            is_cancelled=is_cancelled,
        )
        final = cast(AgentGraphState, await self._graph.ainvoke(initial))
        return AgentRunResult(
            final_text=final["final_text"],
            status=AgentRunStatus(final["status"]),
            turns=final["turns"],
            tool_calls=final["tool_calls_used"],
            usage=ModelUsage(input_tokens=final["input_tokens"], output_tokens=final["output_tokens"]),
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
        builder.add_edge(START, NODE_PREPARE_CONTEXT)
        builder.add_edge(NODE_PREPARE_CONTEXT, NODE_MODEL)
        builder.add_conditional_edges(
            NODE_MODEL,
            self._route_after_model,
            {NODE_TOOLS: NODE_TOOLS, NODE_FINALIZE: NODE_FINALIZE},
        )
        builder.add_conditional_edges(
            NODE_TOOLS,
            self._route_after_tools,
            {NODE_PREPARE_CONTEXT: NODE_PREPARE_CONTEXT, NODE_FINALIZE: NODE_FINALIZE},
        )
        builder.add_edge(NODE_FINALIZE, END)
        return builder.compile()

    async def _prepare_context(self, state: AgentGraphState) -> AgentGraphState:
        self._ensure_runnable(state)
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
        response = await self._complete_with_recovery(
            state,
            ModelRequest(
                model_id=state["model_id"],
                messages=tuple(state["messages"]),
                tools=self._registry.list(),
                temperature=state["temperature"],
                max_tokens=state["max_tokens"],
                params=state["params"],
            ),
        )
        assistant = ModelMessage(
            role=ModelRole.ASSISTANT,
            content=response.content,
            tool_calls=response.tool_calls,
        )
        return {
            **state,
            "messages": [*state["messages"], assistant],
            "pending_tool_calls": list(response.tool_calls),
            "final_text": response.content,
            "turns": state["turns"] + 1,
            "input_tokens": state["input_tokens"] + (response.input_tokens or 0),
            "output_tokens": state["output_tokens"] + (response.output_tokens or 0),
        }

    async def _execute_tools(self, state: AgentGraphState) -> AgentGraphState:
        self._ensure_runnable(state)
        messages = list(state["messages"])
        used = state["tool_calls_used"]
        exhausted = False
        for call in state["pending_tool_calls"]:
            if used >= state["policy"].max_tool_calls:
                exhausted = True
                messages.append(_tool_message(call.id, TOOL_BUDGET_EXHAUSTED))
                continue
            messages.append(await self._run_tool_call(call))
            used += 1
        return {
            **state,
            "messages": messages,
            "pending_tool_calls": [],
            "tool_calls_used": used,
            "budget_exhausted": state["budget_exhausted"] or exhausted,
        }

    async def _run_tool_call(self, call: ModelToolCall) -> ModelMessage:
        arguments = dict(call.arguments)
        try:
            context = await self._hooks.run(
                HookEvent.PRE_TOOL_USE,
                {"call_id": call.id, "tool": call.name, "arguments": arguments},
            )
        except Exception as exc:
            return _tool_message(call.id, TOOL_BLOCKED_TEMPLATE.format(reason=f"{type(exc).__name__}: {exc}"))
        payload = context.payload.get("arguments", arguments)
        if isinstance(payload, Mapping):
            arguments = dict(payload)
        return await self._execute_tool(call, arguments)

    async def _execute_tool(self, call: ModelToolCall, arguments: dict[str, Any]) -> ModelMessage:
        try:
            definition = self._registry.get(call.name)
        except ToolNotFoundError:
            return _tool_message(call.id, UNKNOWN_TOOL_TEMPLATE.format(name=call.name))
        if definition.handler is None:
            return _tool_message(call.id, TOOL_FAILED_TEMPLATE.format(reason="no handler registered"))
        try:
            content = await definition.handler(arguments)
        except Exception as exc:
            content = TOOL_FAILED_TEMPLATE.format(reason=f"{type(exc).__name__}: {exc}")
        context = await self._hooks.run(
            HookEvent.POST_TOOL_USE,
            {"call_id": call.id, "tool": call.name, "arguments": arguments, "result": content},
        )
        result = context.payload.get("result", content)
        if isinstance(result, str):
            content = result
        return _tool_message(call.id, content)

    async def _finalize(self, state: AgentGraphState) -> AgentGraphState:
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
            "status": str(
                AgentRunStatus.BUDGET_EXCEEDED if exceeded else AgentRunStatus.COMPLETED
            ),
        }

    def _route_after_model(self, state: AgentGraphState) -> str:
        if not state["pending_tool_calls"]:
            return NODE_FINALIZE
        if state["turns"] >= state["policy"].max_turns:
            return NODE_FINALIZE
        return NODE_TOOLS

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
        if elapsed_ms >= state["policy"].deadline_ms:
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
                return await self._provider.complete(request)
            except ModelRateLimitedError as exc:
                await asyncio.sleep(self._retry_delay(state, attempts, exc.retry_after))
                attempts += 1
            except ModelUnavailableError:
                await asyncio.sleep(self._retry_delay(state, attempts, None))
                attempts += 1
            except ModelRequestError as exc:
                raise RunnerModelError(f"model request rejected: {exc}") from exc

    def _retry_delay(
        self,
        state: AgentGraphState,
        attempt: int,
        retry_after: float | None,
    ) -> float:
        policy = state["policy"]
        if attempt >= policy.max_model_retries:
            raise RunnerModelError(f"model retries exhausted: {attempt}")
        delay = retry_after if retry_after is not None else DEFAULT_RETRY_BASE_SEC * (2**attempt)
        elapsed_ms = (time.monotonic() - state["started_at"]) * 1000
        if elapsed_ms + delay * 1000 >= policy.deadline_ms:
            raise RunnerDeadlineExceeded("retry would exceed the run deadline")
        return delay
