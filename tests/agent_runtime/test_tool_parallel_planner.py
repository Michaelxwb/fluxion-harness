"""[S-04][B-06][RULE-mcp-001 引用] 安全 READ 有界并行：真实 Runner + 可控异步工具处理器。

不得 Mock 的真实边界：`AgentRunner`（真实图执行 / 回合收口 / 预算预留）、真实 `ToolRegistry`
并发声明、真实 asyncio 屏障（证明两个 independent READ 同时在飞）、独立空库
（`async_tool_database`）。规划器与声明是 agent-core 的纯逻辑，Runner 侧用可注入的异步
处理器观察启动屏障、写屏障与返回顺序。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from muad_agent_core.agent import AgentPolicy, AgentRunner, AgentRunRequest, AgentRunStatus
from muad_agent_core.model import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    text_of,
)
from muad_agent_core.tools import ToolConcurrency, ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_core.tools.planner import PlannedToolCall, plan_tool_batches


class ScriptedModelProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def _text(content: str) -> ModelResponse:
    return ModelResponse(content=content, finish_reason="stop", input_tokens=1, output_tokens=1)


def _tool_calls(*specs: tuple[str, str, dict[str, Any]]) -> ModelResponse:
    return ModelResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=tuple(
            ModelToolCall(id=call_id, name=name, arguments=arguments)
            for call_id, name, arguments in specs
        ),
    )


class ToolProbe:
    """可控异步处理器：记录同时在飞数与起止顺序，可用屏障强制「两方都到齐」。"""

    def __init__(self, *, sleep_sec: float = 0.02) -> None:
        self.entered = 0
        self.max_concurrent = 0
        self.events: list[tuple[str, str]] = []
        self.barriers: dict[str, asyncio.Barrier] = {}
        self.sleep_overrides: dict[str, float] = {}
        self.sleep_sec = sleep_sec

    async def run(self, call_id: str) -> str:
        self.entered += 1
        self.max_concurrent = max(self.max_concurrent, self.entered)
        self.events.append((call_id, "start"))
        try:
            barrier = self.barriers.get(call_id)
            if barrier is not None:
                # 串行执行时对端永远不会到：超时显式失败，不让套件挂死。
                await asyncio.wait_for(barrier.wait(), timeout=2.0)
            await asyncio.sleep(self.sleep_overrides.get(call_id, self.sleep_sec))
            return f"result:{call_id}"
        finally:
            self.entered -= 1
            self.events.append((call_id, "end"))

    def index(self, call_id: str, phase: str) -> int:
        return self.events.index((call_id, phase))


def _define(
    name: str,
    probe: ToolProbe,
    *,
    effect: ToolEffect = ToolEffect.READ,
    concurrency: ToolConcurrency | None = None,
    resource_key: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> ToolDefinition:
    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return await probe.run(call_id)

    kwargs: dict[str, Any] = {}
    if concurrency is not None:
        kwargs["concurrency"] = concurrency
    if resource_key is not None:
        kwargs["resource_key"] = resource_key
    return ToolDefinition(
        name=name,
        description=name,
        input_schema={"type": "object"},
        effect=effect,
        handler=handler,
        **kwargs,
    )


def _read(name: str, probe: ToolProbe, resource: str) -> ToolDefinition:
    return _define(
        name,
        probe,
        concurrency=ToolConcurrency.PARALLEL_READ,
        resource_key=lambda arguments: resource,
    )


def _by_argument_key(arguments: Mapping[str, Any]) -> str | None:
    value = arguments.get("key")
    return f"key:{value}" if isinstance(value, str) else None


def _unknown_key(arguments: Mapping[str, Any]) -> str | None:
    return None


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        model_id="gpt-4o-mini",
        instructions="be helpful",
        messages=(ModelMessage(role=ModelRole.USER, content="hi"),),
        policy=AgentPolicy(),
    )


def _tool_messages(provider: ScriptedModelProvider, index: int) -> list[ModelMessage]:
    return [message for message in provider.requests[index].messages if message.role is ModelRole.TOOL]


@pytest.mark.integration
async def test_s04_independent_reads_meet_barrier_and_write_starts_after_batch(async_tool_database) -> None:
    """[S-04] 两个允许并发的独立 READ 均到启动屏障；WRITE 在整批之后；返回按原调用序。"""
    probe = ToolProbe()
    registry = ToolRegistry()
    registry.register(_read("read_alpha", probe, "alpha"))
    registry.register(_read("read_beta", probe, "beta"))
    registry.register(_define("write_state", probe, effect=ToolEffect.WRITE))
    shared_barrier = asyncio.Barrier(2)
    probe.barriers["c1"] = shared_barrier
    probe.barriers["c2"] = shared_barrier
    # 完成顺序刻意反转为 c2 先于 c1：消息顺序仍须按原调用序。
    probe.sleep_overrides.update({"c1": 0.06, "c2": 0.01})
    provider = ScriptedModelProvider(
        [
            _tool_calls(("c1", "read_alpha", {}), ("c2", "read_beta", {}), ("c3", "write_state", {})),
            _text("done"),
        ]
    )
    runner = AgentRunner(provider=provider, registry=registry, parallel_limit=2)

    result = await runner.run(_request())

    assert result.status == AgentRunStatus.COMPLETED and result.final_text == "done"
    assert probe.max_concurrent == 2, "两个独立 READ 未同时在飞（启动屏障无法被填满）"
    both_started = max(probe.index("c1", "start"), probe.index("c2", "start"))
    first_end = min(probe.index("c1", "end"), probe.index("c2", "end"))
    assert both_started < first_end, "两个 READ 未同时在飞"
    assert probe.index("c2", "end") < probe.index("c1", "end"), "前置条件不成立：完成顺序未反转"
    assert probe.index("c3", "start") > max(
        probe.index("c1", "end"), probe.index("c2", "end")
    ), "WRITE 在 READ 批结束前启动"
    messages = _tool_messages(provider, 1)
    assert [message.tool_call_id for message in messages] == ["c1", "c2", "c3"]
    assert [text_of(message.content) for message in messages] == ["result:c1", "result:c2", "result:c3"]


@pytest.mark.integration
async def test_b06_undeclared_read_stays_serial(async_tool_database) -> None:
    """[B-06] READ 未声明并发（默认 SERIAL）保持串行。"""
    probe = ToolProbe()
    registry = ToolRegistry()
    registry.register(_define("plain_read", probe))
    provider = ScriptedModelProvider(
        [_tool_calls(("c1", "plain_read", {}), ("c2", "plain_read", {})), _text("done")]
    )
    runner = AgentRunner(provider=provider, registry=registry)

    await runner.run(_request())

    assert probe.max_concurrent == 1
    assert probe.events == [("c1", "start"), ("c1", "end"), ("c2", "start"), ("c2", "end")]
    assert [message.tool_call_id for message in _tool_messages(provider, 1)] == ["c1", "c2"]


@pytest.mark.integration
async def test_b06_shared_stateful_resource_stays_serial(async_tool_database) -> None:
    """[B-06] 共享有状态资源：同资源键的 PARALLEL_READ 仍串行。"""
    probe = ToolProbe()
    registry = ToolRegistry()
    registry.register(_read("shared_read", probe, "shared-state"))
    provider = ScriptedModelProvider(
        [_tool_calls(("c1", "shared_read", {}), ("c2", "shared_read", {})), _text("done")]
    )
    runner = AgentRunner(provider=provider, registry=registry)

    await runner.run(_request())

    assert probe.max_concurrent == 1
    assert probe.events == [("c1", "start"), ("c1", "end"), ("c2", "start"), ("c2", "end")]


@pytest.mark.integration
async def test_b06_same_resource_lock_serializes_only_conflicting_calls(async_tool_database) -> None:
    """[B-06] 同资源锁拆分批次：同键调用串行，异键调用可重叠。"""
    probe = ToolProbe()
    registry = ToolRegistry()
    registry.register(
        _define(
            "keyed_read",
            probe,
            concurrency=ToolConcurrency.PARALLEL_READ,
            resource_key=_by_argument_key,
        )
    )
    provider = ScriptedModelProvider(
        [
            _tool_calls(
                ("c1", "keyed_read", {"key": "one"}),
                ("c2", "keyed_read", {"key": "one"}),
                ("c3", "keyed_read", {"key": "two"}),
            ),
            _text("done"),
        ]
    )
    runner = AgentRunner(provider=provider, registry=registry)

    await runner.run(_request())

    assert probe.index("c1", "end") < probe.index("c2", "start"), "同资源键调用未串行"
    assert probe.max_concurrent == 2, "异资源键调用未重叠"
    assert [message.tool_call_id for message in _tool_messages(provider, 1)] == ["c1", "c2", "c3"]


@pytest.mark.integration
async def test_b06_unknown_dependency_stays_serial(async_tool_database) -> None:
    """[B-06] 未知依赖（资源键返回 None）保持串行。"""
    probe = ToolProbe()
    registry = ToolRegistry()
    registry.register(
        _define(
            "unknown_read",
            probe,
            concurrency=ToolConcurrency.PARALLEL_READ,
            resource_key=_unknown_key,
        )
    )
    provider = ScriptedModelProvider(
        [_tool_calls(("c1", "unknown_read", {}), ("c2", "unknown_read", {})), _text("done")]
    )
    runner = AgentRunner(provider=provider, registry=registry)

    await runner.run(_request())

    assert probe.max_concurrent == 1
    assert probe.events == [("c1", "start"), ("c1", "end"), ("c2", "start"), ("c2", "end")]


@pytest.mark.integration
async def test_b06_parallel_limit_one_stays_serial(async_tool_database) -> None:
    """[B-06] 并行度=1：声明可并发的独立 READ 仍逐条执行。"""
    probe = ToolProbe()
    registry = ToolRegistry()
    registry.register(_read("read_alpha", probe, "alpha"))
    registry.register(_read("read_beta", probe, "beta"))
    provider = ScriptedModelProvider(
        [_tool_calls(("c1", "read_alpha", {}), ("c2", "read_beta", {})), _text("done")]
    )
    runner = AgentRunner(provider=provider, registry=registry, parallel_limit=1)

    await runner.run(_request())

    assert probe.max_concurrent == 1
    assert probe.events == [("c1", "start"), ("c1", "end"), ("c2", "start"), ("c2", "end")]


@pytest.mark.integration
async def test_b06_parallel_limit_bounds_batch(async_tool_database) -> None:
    """[B-06] 上限边界：批大小不超过 parallel_limit，超出部分等前批结束后再跑。"""
    probe = ToolProbe()
    registry = ToolRegistry()
    registry.register(_read("read_alpha", probe, "alpha"))
    registry.register(_read("read_beta", probe, "beta"))
    registry.register(_read("read_gamma", probe, "gamma"))
    provider = ScriptedModelProvider(
        [
            _tool_calls(
                ("c1", "read_alpha", {}),
                ("c2", "read_beta", {}),
                ("c3", "read_gamma", {}),
            ),
            _text("done"),
        ]
    )
    runner = AgentRunner(provider=provider, registry=registry, parallel_limit=2)

    await runner.run(_request())

    assert probe.max_concurrent == 2, "同时执行数超过 parallel_limit"
    assert probe.index("c3", "start") > max(
        probe.index("c1", "end"), probe.index("c2", "end")
    ), "超限调用未等前批结束"
    assert [message.tool_call_id for message in _tool_messages(provider, 1)] == ["c1", "c2", "c3"]


@pytest.mark.integration
@pytest.mark.parametrize("value", [0, 17, True, 2.5])
async def test_b06_parallel_limit_rejects_invalid_values(async_tool_database, value: Any) -> None:
    """[B-06] 上限校验：bool/小数/越界值在构造期显式拒绝，不静默夹取。"""
    with pytest.raises(ValueError):
        AgentRunner(
            provider=ScriptedModelProvider([]),
            registry=ToolRegistry(),
            parallel_limit=value,
        )


@pytest.mark.unit
def test_b06_tool_definition_defaults_to_serial() -> None:
    """[B-06] 并发声明不可由模型设置：默认 SERIAL，声明缺失时不并发。"""

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return "ok"

    definition = ToolDefinition(
        name="plain",
        description="plain",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
        handler=handler,
    )
    assert definition.concurrency is ToolConcurrency.SERIAL
    assert definition.resource_key is None


@pytest.mark.unit
def test_b06_planner_splits_consecutive_batches_by_declaration_and_order() -> None:
    """[B-06] 规划器保持原调用序：同键冲突/写屏障/未知依赖把连续 READ 拆成连续批。"""

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read",
            description="read",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
            concurrency=ToolConcurrency.PARALLEL_READ,
            resource_key=_by_argument_key,
        )
    )
    registry.register(
        ToolDefinition(
            name="write",
            description="write",
            input_schema={"type": "object"},
            effect=ToolEffect.WRITE,
            handler=handler,
        )
    )
    calls = (
        PlannedToolCall("c0", "read", {"key": "one"}),
        PlannedToolCall("c1", "read", {"key": "one"}),
        PlannedToolCall("c2", "read", {"key": "two"}),
        PlannedToolCall("c3", "write", {}),
        PlannedToolCall("c4", "read", {"key": "three"}),
    )

    batches = plan_tool_batches(calls, registry=registry, parallel_limit=4)

    assert [([call.call_id for call in batch.calls], batch.parallel) for batch in batches] == [
        (["c0"], False),
        (["c1", "c2"], True),
        (["c3"], False),
        (["c4"], False),
    ]
