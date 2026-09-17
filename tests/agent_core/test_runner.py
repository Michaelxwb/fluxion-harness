import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

import pytest
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    AgentRunStatus,
    RunnerCancelled,
    RunnerDeadlineExceeded,
    RunnerModelError,
)
from muad_agent_core.hooks import HookEvent, HookPipeline
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRateLimitedError,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelUnavailableError,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry


class ScriptedModelProvider:
    def __init__(self, script: list[ModelResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("unexpected model call")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _text(content: str, *, input_tokens: int = 1, output_tokens: int = 1) -> ModelResponse:
    return ModelResponse(
        content=content,
        finish_reason="stop",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _tool_call(
    call_id: str,
    name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> ModelResponse:
    return ModelResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=(
            ModelToolCall(id=call_id, name=name, arguments=dict(arguments or {})),
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _echo_tool(registry: ToolRegistry, calls: list[dict[str, Any]]) -> None:
    async def handler(arguments: Mapping[str, Any]) -> str:
        calls.append(dict(arguments))
        return json.dumps({"echo": arguments.get("text")})

    registry.register(
        ToolDefinition(
            name="echo",
            description="echoes text",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )


def _request(policy: AgentPolicy | None = None) -> AgentRunRequest:
    return AgentRunRequest(
        model_id="gpt-4o-mini",
        instructions="be helpful",
        messages=(ModelMessage(role=ModelRole.USER, content="hi"),),
        policy=policy or AgentPolicy(),
    )


async def test_tool_call_then_final_answer_and_hook_order() -> None:
    provider = ScriptedModelProvider([_tool_call("c1", "echo", {"text": "ping"}), _text("done")])
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []
    _echo_tool(registry, calls)
    events: list[str] = []
    hooks = HookPipeline()

    async def record(context: Any) -> None:
        events.append(str(context.event))

    for event in HookEvent:
        hooks.register(event, record)

    runner = AgentRunner(provider=provider, registry=registry, hooks=hooks)
    result = await runner.run(_request())

    assert result.final_text == "done"
    assert result.status is AgentRunStatus.COMPLETED
    assert result.turns == 2
    assert result.tool_calls == 1
    assert calls == [{"text": "ping"}]
    assert events == [
        HookEvent.USER_PROMPT.value,
        HookEvent.PRE_TOOL_USE.value,
        HookEvent.POST_TOOL_USE.value,
        HookEvent.USER_PROMPT.value,
        HookEvent.STOP.value,
    ]
    assert [str(message.role) for message in provider.requests[0].messages][0] == "system"
    assert "be helpful" in provider.requests[0].messages[0].content
    assert provider.requests[1].messages[-1].role is ModelRole.TOOL
    assert provider.requests[1].messages[-1].tool_call_id == "c1"


async def test_tool_budget_stops_further_execution() -> None:
    provider = ScriptedModelProvider([_tool_call("c1", "echo"), _tool_call("c2", "echo")])
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []
    _echo_tool(registry, calls)

    runner = AgentRunner(provider=provider, registry=registry)
    result = await runner.run(_request(AgentPolicy(max_tool_calls=1)))

    assert result.status is AgentRunStatus.BUDGET_EXCEEDED
    assert result.tool_calls == 1
    assert calls == [{}]
    assert len(provider.requests) == 2


async def test_max_turns_budget_finalizes_without_extra_model_call() -> None:
    provider = ScriptedModelProvider([_tool_call("c1", "echo")])
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []
    _echo_tool(registry, calls)

    runner = AgentRunner(provider=provider, registry=registry)
    result = await runner.run(_request(AgentPolicy(max_turns=1, max_tool_calls=5)))

    assert result.status is AgentRunStatus.BUDGET_EXCEEDED
    assert result.turns == 1
    assert calls == []


async def test_unknown_tool_is_fed_back_to_model() -> None:
    provider = ScriptedModelProvider([_tool_call("c1", "missing"), _text("recovered")])
    runner = AgentRunner(provider=provider, registry=ToolRegistry())

    result = await runner.run(_request())

    assert result.final_text == "recovered"
    assert result.tool_calls == 1
    tool_message = provider.requests[1].messages[-1]
    assert tool_message.role is ModelRole.TOOL
    assert "unknown tool: missing" in tool_message.content


async def test_tool_handler_failure_is_fed_back_to_model() -> None:
    async def failing(arguments: Mapping[str, Any]) -> str:
        raise RuntimeError("handler exploded")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="boom",
            description="fails",
            input_schema={"type": "object"},
            effect=ToolEffect.WRITE,
            handler=failing,
        )
    )
    provider = ScriptedModelProvider([_tool_call("c1", "boom"), _text("recovered")])

    result = await AgentRunner(provider=provider, registry=registry).run(_request())

    assert result.final_text == "recovered"
    assert "handler exploded" in provider.requests[1].messages[-1].content


async def test_pre_tool_use_hook_can_block_execution() -> None:
    provider = ScriptedModelProvider([_tool_call("c1", "echo"), _text("done")])
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []
    _echo_tool(registry, calls)
    hooks = HookPipeline()

    async def block(context: Any) -> None:
        raise PermissionError("denied by policy")

    hooks.register(HookEvent.PRE_TOOL_USE, block)

    result = await AgentRunner(provider=provider, registry=registry, hooks=hooks).run(_request())

    assert calls == []
    assert result.final_text == "done"
    assert "denied by policy" in provider.requests[1].messages[-1].content


async def test_unavailable_model_is_retried_with_backoff() -> None:
    provider = ScriptedModelProvider(
        [
            ModelUnavailableError("down"),
            ModelUnavailableError("down"),
            _text("ok"),
        ]
    )

    result = await AgentRunner(provider=provider, registry=ToolRegistry()).run(_request())

    assert result.final_text == "ok"
    assert len(provider.requests) == 3


async def test_unavailable_model_fails_after_retry_budget() -> None:
    provider = ScriptedModelProvider([ModelUnavailableError("down"), ModelUnavailableError("down")])
    policy = AgentPolicy(max_model_retries=1)

    with pytest.raises(RunnerModelError):
        await AgentRunner(provider=provider, registry=ToolRegistry()).run(_request(policy))

    assert len(provider.requests) == 2


async def test_rate_limit_honors_retry_after() -> None:
    provider = ScriptedModelProvider(
        [ModelRateLimitedError("slow down", retry_after=0.05), _text("ok")]
    )

    started = time.monotonic()
    result = await AgentRunner(provider=provider, registry=ToolRegistry()).run(_request())
    elapsed = time.monotonic() - started

    assert result.final_text == "ok"
    assert elapsed >= 0.05


async def test_deadline_exceeded_between_steps() -> None:
    class SlowProvider:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            await asyncio.sleep(0.05)
            return _tool_call("c1", "echo")

    registry = ToolRegistry()
    _echo_tool(registry, [])
    policy = AgentPolicy(deadline_ms=30)

    with pytest.raises(RunnerDeadlineExceeded):
        await AgentRunner(provider=SlowProvider(), registry=registry).run(_request(policy))


async def test_external_cancellation_is_checked_between_steps() -> None:
    provider = ScriptedModelProvider([_tool_call("c1", "echo"), _text("never")])
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []
    state = {"cancelled": False}

    async def handler(arguments: Mapping[str, Any]) -> str:
        calls.append(dict(arguments))
        state["cancelled"] = True
        return "{}"

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )

    with pytest.raises(RunnerCancelled):
        await AgentRunner(provider=provider, registry=registry).run(
            _request(),
            is_cancelled=lambda: state["cancelled"],
        )

    assert calls == [{}]
    assert len(provider.requests) == 1


async def test_asyncio_cancellation_propagates() -> None:
    entered = asyncio.Event()

    class BlockingProvider:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            entered.set()
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    runner = AgentRunner(provider=BlockingProvider(), registry=ToolRegistry())
    task = asyncio.create_task(runner.run(_request()))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_policy_from_runtime_config_reads_valid_overrides() -> None:
    policy = AgentPolicy.from_runtime_config(
        {"max_turns": 5, "max_tool_calls": True, "deadline_ms": "soon", "max_model_retries": 0}
    )

    assert policy.max_turns == 5
    assert policy.max_tool_calls == 30
    assert policy.deadline_ms == 120_000
    assert policy.max_model_retries == 3


def test_model_provider_protocol_accepts_scripted_provider() -> None:
    provider: ModelProvider = ScriptedModelProvider([_text("x")])
    assert provider is not None
