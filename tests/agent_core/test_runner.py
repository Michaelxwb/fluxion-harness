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
from muad_agent_core.context.compactor import split_groups
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
    text_of,
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
    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
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


def _multi_tool_call(*calls: tuple[str, str]) -> ModelResponse:
    """一次模型响应里声明**多个**工具调用（元组顺序即执行顺序）。"""
    return ModelResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=tuple(
            ModelToolCall(id=call_id, name=name, arguments={}) for call_id, name in calls
        ),
    )


def _image_tool(registry: ToolRegistry) -> None:
    """带 `follow_up_messages` 的工具（`view_image` 的形态）：结果之后补一条 user 消息。"""

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return f"IMG:{call_id}"

    async def follow_up(result: str) -> tuple[ModelMessage, ...]:
        return (ModelMessage(role=ModelRole.USER, content="[重看附件] pic.png"),)

    registry.register(
        ToolDefinition(
            name="view_image",
            description="re-open an image attachment",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
            follow_up_messages=follow_up,
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
    # 07/08：Hook 事件扩展后，注册哪些就观测哪些；pre/post_model 仅在注册时出现
    assert [e for e in events if e in {
        HookEvent.USER_PROMPT.value, HookEvent.PRE_TOOL_USE.value,
        HookEvent.POST_TOOL_USE.value, HookEvent.STOP.value,
    }] == [
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


async def test_rule01_follow_up_message_does_not_orphan_sibling_tool_result() -> None:
    """[RULE-01] 同轮「看图 + 其他工具」：补消息落在**回合末**，两条结果都留得住。

    补消息是 `user` 角色（tool 角色带不了图像块）。它若插在两条 tool 结果之间，后面那条就与
    声明它的 assistant 隔开了 —— 压缩器的 `split_groups` 只把「当前组声明过的」tool 消息收进组，
    被隔开的那条会被当孤儿**整条丢掉**（2026-10-06 review 实测：同轮 search 结果消失）。
    """
    provider = ScriptedModelProvider(
        [_multi_tool_call(("c1", "view_image"), ("c2", "echo")), _text("done")]
    )
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []
    _echo_tool(registry, calls)
    _image_tool(registry)

    runner = AgentRunner(provider=provider, registry=registry)
    result = await runner.run(_request())

    assert result.final_text == "done"
    assert result.tool_calls == 2
    sent = provider.requests[1].messages
    # ① 两条工具结果都在，且顺序与声明一致
    assert [m.tool_call_id for m in sent if m.role is ModelRole.TOOL] == ["c1", "c2"]
    # ② 补消息在整回合之后，不在两条结果中间
    assert sent[-1].role is ModelRole.USER
    assert "重看附件" in text_of(sent[-1].content)
    # ③ 压缩器的分组视角：整回合仍是一个不可分单元，没有任何 tool 消息被判成孤儿
    round_group = [group for group in split_groups(sent) if any(m.tool_calls for m in group)]
    assert len(round_group) == 1
    assert {m.tool_call_id for m in round_group[0] if m.role is ModelRole.TOOL} == {"c1", "c2"}


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
    async def failing(arguments: Mapping[str, Any], *, call_id: str) -> str:
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


class _CodedToolError(RuntimeError):
    """模拟 `AttachmentToolError` / `ArchiveToolError`：工具自己的错误类型，携带 `.code`。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


async def test_coded_tool_failure_reaches_the_model_with_its_code() -> None:
    """带 `.code` 的工具错误必须与「返回错误体」的工具**同形**到达模型。

    `skill_tools`/`task_tools`/`memory_tools` 在自己那层把领域错误转成
    `{"error": {"code", "message"}}` **返回**；attachments/archive 则是**抛出**带 `.code`
    的自己人错误。此前后者只被拼成 `tool failed: AttachmentToolError: …` —— 同一件事两套
    说法，而且模型看不到错误码（2026-10-03 review）。
    """
    async def failing(arguments: Mapping[str, Any], *, call_id: str) -> str:
        raise _CodedToolError("ATTACHMENT_NOT_FOUND", "附件不存在或不可访问")

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
    payload = json.loads(provider.requests[1].messages[-1].content)
    assert payload == {
        "error": {"code": "ATTACHMENT_NOT_FOUND", "message": "附件不存在或不可访问"}
    }


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


async def test_deadline_is_enforced_on_the_in_flight_model_call() -> None:
    """[ADR-07] 总 deadline 约束**正在飞的那次调用**：超了就不许以 COMPLETED 收尾。

    修复前 `_ensure_runnable` 只在步与步之间检查，模型调用返回后直接进 finalize ⇒ deadline 20ms、
    调用 ~50ms 的 Run 照样 COMPLETED（2026-10-06 review 实测：20ms 配 95ms 调用 → COMPLETED）。
    """

    class _SlowProvider:
        def __init__(self) -> None:
            self.request: ModelRequest | None = None

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.request = request
            await asyncio.sleep(0.05)
            return _text("done")

    provider = _SlowProvider()
    runner = AgentRunner(provider=provider, registry=ToolRegistry())

    with pytest.raises(RunnerDeadlineExceeded):
        await runner.run(_request(AgentPolicy(deadline_ms=20)))

    # 逐请求超时按**剩余总预算**派生（ADR-07：单次请求预算 = min(上限, 剩余总预算)）
    assert provider.request is not None and provider.request.timeout_sec is not None
    assert 0 < provider.request.timeout_sec <= 0.02


async def test_external_cancellation_is_checked_between_steps() -> None:
    provider = ScriptedModelProvider([_tool_call("c1", "echo"), _text("never")])
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []
    state = {"cancelled": False}

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
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


class RecordingCompactor:
    """可观察的压缩假件：记下每次收到的派生历史，并在末尾接一条省略标记。"""

    def __init__(self) -> None:
        self.seen: list[tuple[ModelMessage, ...]] = []

    async def compact(self, messages: Any) -> tuple[ModelMessage, ...]:
        received = tuple(messages)
        self.seen.append(received)
        return (*received, ModelMessage(role=ModelRole.SYSTEM, content="[历史省略] 中间已压缩"))


async def test_compaction_rewrites_only_the_outbound_request() -> None:
    """压缩挂在 `ModelRequest` 的唯一组装点：改的是发出去的那份，`state` 里的权威历史不动。"""
    provider = ScriptedModelProvider([_text("done")])
    compactor = RecordingCompactor()
    runner = AgentRunner(
        provider=provider, registry=ToolRegistry(), context_compactor=compactor
    )

    await runner.run(_request())

    sent = provider.requests[0].messages
    assert sent[-1].content == "[历史省略] 中间已压缩", "压缩产物必须进模型请求"
    # 压缩器收到的就是组装好的那份（系统提示 + 历史），不是另一份口径
    assert [m.role for m in compactor.seen[0]] == [ModelRole.SYSTEM, ModelRole.USER]


async def test_compaction_does_not_leak_back_into_the_authoritative_history() -> None:
    """多回合下每轮都从权威历史重算：上一轮压出来的标记**不得沉淀**进 `state["messages"]`。

    这正是"重建的那份 == 真正发出去的那份"能成立的前提——`state` 只有一个版本，压缩是纯派生。
    """
    provider = ScriptedModelProvider([_tool_call("c1", "echo", {"text": "ping"}), _text("done")])
    registry = ToolRegistry()
    _echo_tool(registry, [])
    compactor = RecordingCompactor()
    runner = AgentRunner(
        provider=provider, registry=registry, context_compactor=compactor
    )

    await runner.run(_request())

    assert len(compactor.seen) == 2, "两个模型回合各压一次"
    assert [m.role for m in compactor.seen[1]] == [
        ModelRole.SYSTEM,
        ModelRole.USER,
        ModelRole.ASSISTANT,
        ModelRole.TOOL,
    ], "第二轮的输入是原始历史 + 本轮工具回合，没有上一轮的压缩产物"
    assert all("省略" not in str(m.content) for m in compactor.seen[1])


async def test_incomplete_model_stream_is_not_reported_as_success() -> None:
    """[审查 2026-10-06] provider 报"没有收尾信息"（空 finish_reason）⇒ 不得落成 COMPLETED。

    半截流被当成功会让残缺回答直接变成 Run 的终态。这里**不重试**：增量已经外发，重试等于把
    同一段话再吐一遍。
    """

    class _TruncatedProvider:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(content="说到一半", finish_reason="")

    runner = AgentRunner(provider=_TruncatedProvider(), registry=ToolRegistry())

    with pytest.raises(RunnerModelError):
        await runner.run(_request())
