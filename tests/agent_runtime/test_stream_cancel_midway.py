"""流式生成中途取消：每块增量外发前都判一次 `is_cancelled`，不能等整段吐完。

现场：用户点取消后回答仍在持续输出，库里 CANCEL 事件紧贴 MODEL_CALL_COMPLETED。
根因是 provider 的 `async for` 读流循环只累积 token、从不看 `is_cancelled`，
取消只能等 `streamer()` 返回后才在流后检查点生效。
"""

from __future__ import annotations

import pytest
from muad_agent_core.agent import AgentRunRequest, AgentRunner, RunnerCancelled
from muad_agent_core.model import ModelRequest, ModelResponse
from muad_agent_core.tools import ToolRegistry


class MultiChunkProvider:
    """分多块吐字；`cancel_after` 块之后翻转取消标记，模拟用户中途点取消。"""

    def __init__(self, chunks: tuple[str, ...], *, cancel_after: int) -> None:
        self._chunks = chunks
        self._cancel_after = cancel_after
        self.cancelled = False
        self.deltas_delivered = 0
        self.completed = False

    def is_cancelled(self) -> bool:
        return self.cancelled

    async def stream(self, request: ModelRequest, on_delta) -> ModelResponse:
        for index, chunk in enumerate(self._chunks):
            if index == self._cancel_after:
                self.cancelled = True
            await on_delta(chunk)
            self.deltas_delivered += 1
        self.completed = True
        return ModelResponse(
            content="".join(self._chunks), finish_reason="stop", input_tokens=1, output_tokens=1
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("非流式路径不应被触发")


async def test_cancel_midstream_stops_before_remaining_chunks() -> None:
    provider = MultiChunkProvider(("第一段", "第二段", "第三段"), cancel_after=1)
    runner = AgentRunner(provider=provider, registry=ToolRegistry())
    delivered: list[str] = []

    async def on_delta(text: str) -> None:
        delivered.append(text)

    with pytest.raises(RunnerCancelled):
        await runner.run(
            AgentRunRequest(model_id="m", instructions="i"),
            is_cancelled=provider.is_cancelled,
            on_delta=on_delta,
        )

    # 取消标记在第 2 块之前翻转：第 1 块已放出，第 2 块因 `track_delta` 里的取消检查被拦下，
    # provider 没跑到结尾（强烈事实：若不修，completion 会 True 且三块全放）。
    assert delivered == ["第一段"]
    assert provider.completed is False
    assert provider.deltas_delivered == 1


async def test_no_cancel_streams_all_chunks_to_completion() -> None:
    """对照：取消标记从不翻转时，整段正常吐完、返回 COMPLETED。"""
    provider = MultiChunkProvider(("a", "b", "c"), cancel_after=99)
    runner = AgentRunner(provider=provider, registry=ToolRegistry())
    delivered: list[str] = []

    async def on_delta(text: str) -> None:
        delivered.append(text)

    result = await runner.run(
        AgentRunRequest(model_id="m", instructions="i"),
        is_cancelled=provider.is_cancelled,
        on_delta=on_delta,
    )

    assert "".join(delivered) == "abc"
    assert provider.completed is True
    assert result.final_text == "abc"
