"""工具**回合**结束时的整批判定端口（design ADR-04）。

判定单元是"回合"而不是"单条调用"：只有把本轮所有结果放在一起看，才算得出"合计超预算"并按
字节从大到小取舍；而结果一旦逐条外置，产物 id 就随完成事件进了 canonical 行，整轮判定再也插
不进去。所以外置的判定被推到回合末：先由端口决定谁要外置、返回替换后的消息，调用方再逐条报
"这个工具完成了"——产物 id 因此赶得上那条事件。

副作用（落盘 / 写审计 / 发事件）全部在 app 侧实现，`packages/*` 不依赖 app。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..model.provider import ModelMessage


@runtime_checkable
class ToolResultRoundPort(Protocol):
    """给定本回合创建的全部 tool 消息，返回**要替换成什么**（未替换的原样返回）。

    返回序列必须与入参**等长且同序**：调用方按下标回填历史，错位会让工具声明与结果对不上。
    实现方可以原样返回入参（表示"本回合一条都不外置"）。
    """

    async def finish_round(self, results: Sequence[ModelMessage]) -> Sequence[ModelMessage]: ...


__all__ = ["ToolResultRoundPort"]
