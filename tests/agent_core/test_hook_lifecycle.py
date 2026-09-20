"""[S-05] 完整 Hook 生命周期：顺序、异常传播、空注册不干扰、on_interrupt。"""

from __future__ import annotations

from typing import Any

import pytest
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    AgentRunStatus,
)
from muad_agent_core.hooks import HookEvent, HookPipeline
from muad_agent_core.model import ModelMessage, ModelResponse, ModelRole, ModelToolCall
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry


class ScriptedModelProvider:
    def __init__(self, script: list[ModelResponse | Exception]) -> None:
        self._script = list(script)

    async def complete(self, request: Any) -> ModelResponse:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _text(content: str) -> ModelResponse:
    return ModelResponse(
        content=content, finish_reason="stop", input_tokens=1, output_tokens=1
    )


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=(ModelToolCall(id=call_id, name=name, arguments=arguments),),
        input_tokens=1,
        output_tokens=1,
    )


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        model_id="gpt-4o-mini",
        instructions="be helpful",
        messages=(ModelMessage(role=ModelRole.USER, content="hi"),),
        policy=AgentPolicy(),
    )


def _echo_tool(registry: ToolRegistry) -> None:
    async def handler(arguments: dict[str, Any]) -> str:
        return "pong"

    registry.register(
        ToolDefinition(
            name="echo",
            description="echoes",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )


async def test_s05_full_turn_hook_order_with_model_hooks() -> None:
    """[S-05] 一轮完整顺序：user_prompt→pre_model→pre_tool_use→post_tool_use→post_model→stop。"""
    provider = ScriptedModelProvider([_tool_call("c1", "echo", {"text": "ping"}), _text("done")])
    registry = ToolRegistry()
    _echo_tool(registry)
    events: list[str] = []
    hooks = HookPipeline()

    def record(event: HookEvent) -> None:
        async def handler(context: Any) -> None:
            events.append(event.value)

        hooks.register(event, handler)

    for event in (
        HookEvent.USER_PROMPT,
        HookEvent.PRE_MODEL,
        HookEvent.PRE_TOOL_USE,
        HookEvent.POST_TOOL_USE,
        HookEvent.POST_MODEL,
        HookEvent.STOP,
    ):
        record(event)

    runner = AgentRunner(provider=provider, registry=registry, hooks=hooks)
    result = await runner.run(_request())

    assert result.status is AgentRunStatus.COMPLETED
    # 明确触发次数：每轮图循环进入 prepare_context 触发 user_prompt；
    # pre_model/post_model 每次模型调用各一次；tool hooks 每次工具各一次。
    assert events == [
        "user_prompt",      # 第 1 轮进入
        "pre_model",        # 模型调用 1（发起工具）
        "post_model",
        "pre_tool_use",     # 工具 echo
        "post_tool_use",
        "user_prompt",      # 第 2 轮进入（LangGraph 循环既有语义）
        "pre_model",        # 模型调用 2（最终回答）
        "post_model",
        "stop",
    ]


async def test_s05_empty_hooks_do_not_interfere() -> None:
    """[S-05] 空注册：无 Hook 时 runner 正常完成。"""
    provider = ScriptedModelProvider([_text("plain done")])
    runner = AgentRunner(provider=provider, registry=ToolRegistry(), hooks=HookPipeline())
    result = await runner.run(_request())
    assert result.final_text == "plain done"
    assert result.status is AgentRunStatus.COMPLETED


async def test_s05_pre_model_error_propagates_and_stop_observed() -> None:
    """[S-05] pre_model 异常传播终止 Run；stop 在异常路径被观测到（可观测收尾）。"""
    provider = ScriptedModelProvider([_text("should not matter")])
    registry = ToolRegistry()
    events: list[str] = []
    hooks = HookPipeline()

    async def boom(context: Any) -> None:
        events.append("pre_model")
        raise RuntimeError("policy veto")

    async def record_stop(context: Any) -> None:
        events.append("stop")

    hooks.register(HookEvent.PRE_MODEL, boom)
    hooks.register(HookEvent.STOP, record_stop)

    runner = AgentRunner(provider=provider, registry=registry, hooks=hooks)
    with pytest.raises(RuntimeError, match="policy veto"):
        await runner.run(_request())
    assert "pre_model" in events
    assert "stop" in events  # 取消/错误收尾可观测


async def test_s05_on_interrupt_fires_for_clarification() -> None:
    """[S-05 补足 on_interrupt] interrupt 路径触发 on_interrupt Hook。"""
    provider = ScriptedModelProvider([])
    registry = ToolRegistry()
    events: list[str] = []
    hooks = HookPipeline()

    async def record_interrupt(context: Any) -> None:
        events.append("on_interrupt")

    hooks.register(HookEvent.ON_INTERRUPT, record_interrupt)
    runner = AgentRunner(provider=provider, registry=registry, hooks=hooks)

    # Runner 暴露 interrupt 入口（澄清/确认暂停），此处直接驱动 interrupt 通知
    await runner.notify_interrupt({"kind": "CLARIFICATION", "prompt": "which one?"})
    assert events == ["on_interrupt"]
