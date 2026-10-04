import asyncio
import uuid

import pytest
from muad_agent_core.agent import AgentPolicy, AgentRunner, AgentRunRequest, RunnerModelError
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import ModelResponse, ModelToolCall, ModelUnavailableError
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_runtime.application.executor import AgentRunnerExecutor, ExecutorRequest
from muad_agent_runtime.application.run_service import STREAM_BUSINESS_TYPES
from muad_contracts import ResolvedAgent, ResolvedModel

from tests.agent_core.test_runner import ScriptedModelProvider


def test_s402_every_executor_event_has_a_persisted_business_name() -> None:
    """执行器声明的每个事件都必须有业务名。

    没有名字的事件会以**裸 SSE 名**落 `canonical_event.event_type`（`STREAM_BUSINESS_TYPES.get`
    的兜底），而 Console 的 Run 轮廓按业务名过滤——漏一个就在审计视图里冒出一行小写裸名。
    """
    from muad_agent_runtime.application import executor as executor_module

    declared = {
        value
        for name, value in vars(executor_module).items()
        if name.endswith("_EVENT") and isinstance(value, str)
    }
    assert declared - set(STREAM_BUSINESS_TYPES) == set()


class Provider:
    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=(ModelToolCall(id="call-1", name="work", arguments={}),),
            )
        return ModelResponse(content="done", finish_reason="stop")


async def test_s402_model_and_tool_lifecycle_wrap_real_calls():
    registry = ToolRegistry()

    async def work(arguments, *, call_id):
        await asyncio.sleep(0)
        return "worked"

    registry.register(
        ToolDefinition(
            name="work",
            description="work",
            input_schema={"type": "object", "properties": {}},
            effect=ToolEffect.READ,
            handler=work,
        )
    )
    runner = AgentRunner(provider=Provider(), registry=registry, hooks=HookPipeline())
    events = []

    async def started():
        events.append("model.started")

    async def completed():
        events.append("model.completed")

    async def tool_started(call_id, name):
        events.append("tool.started")

    async def tool_completed(*args):
        events.append("tool.completed")

    result = await runner.run(
        AgentRunRequest(model_id="test", instructions=""),
        on_model_started=started,
        on_model_completed=completed,
        on_tool_started=tool_started,
        on_tool_completed=tool_completed,
    )
    assert result.final_text == "done"
    assert events == [
        "model.started",
        "model.completed",
        "tool.started",
        "tool.completed",
        "model.started",
        "model.completed",
    ]


async def test_s402_executor_exposes_model_lifecycle_events():
    runner = AgentRunner(provider=Provider(), registry=ToolRegistry(), hooks=HookPipeline())
    request = ExecutorRequest(
        agent=ResolvedAgent(id=uuid.uuid4(), key="test", revision=1, instructions="", runtime_config={}),
        model=ResolvedModel(
            id=uuid.uuid4(),
            model_id="test",
            revision=1,
            protocol="OPENAI",
            base_url="http://localhost",
            params={},
        ),
        input_text="test",
        is_cancel_requested=_not_cancelled,
    )
    events = [event async for event in AgentRunnerExecutor(runner=runner, request=request).run()]
    assert [event.type for event in events if event.type.startswith("model.")] == [
        "model.started",
        "model.completed",
        "model.started",
        "model.completed",
    ]


async def _not_cancelled():
    return False


async def test_s402_model_events_wrap_every_retry_and_failure():
    events = []

    async def started():
        events.append("started")

    async def completed():
        events.append("completed")

    provider = ScriptedModelProvider([ModelUnavailableError("down"), ModelUnavailableError("down")])
    runner = AgentRunner(provider=provider, registry=ToolRegistry())
    with pytest.raises(RunnerModelError):
        await runner.run(
            AgentRunRequest(model_id="test", instructions="", policy=AgentPolicy(max_model_retries=1)),
            on_model_started=started,
            on_model_completed=completed,
        )
    assert events == ["started", "completed", "started", "completed"]


async def test_s402_cancelled_model_still_closes_lifecycle():
    entered = asyncio.Event()
    closed = asyncio.Event()

    class SlowProvider:
        async def complete(self, request):
            entered.set()
            await asyncio.Event().wait()

    async def completed():
        closed.set()

    runner = AgentRunner(provider=SlowProvider(), registry=ToolRegistry())
    task = asyncio.create_task(
        runner.run(AgentRunRequest(model_id="test", instructions=""), on_model_completed=completed)
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
