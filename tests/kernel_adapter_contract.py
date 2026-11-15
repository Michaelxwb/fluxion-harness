"""共享 Kernel 端口契约：同一组断言在 Runtime 与 Worker 的**真实**适配器上各跑一遍。

设计 §3.1.2 的判据是"同一组 Kernel 契约测试在两个适配器上各跑一遍"，所以断言只允许读
宿主中立的事实（`AgentRunStatus`、`SuspensionRequest` 的字段、`RunnerCheckpoint` 的形状、
模型被调用次数、工具是否真的执行）。宿主差异只通过 `HostAdapter` 暴露的三种东西进入：

- 它挂起的**形状**：`kind`（人类回答 / 工具级等待）、`reason`、`prompt`、待定水位；
- 它的**主工具**：`staging_call()` 生成的模型工具调用——Runtime 是记忆确认，Worker 是异步提交；
- 它的**恢复入口**：`resume()` 从宿主自己的 checkpoint store 读回进度再跑同一个循环。

模型传输不属于宿主适配器契约：脚本化 provider 与 `tests/agent_core` 的 Kernel 契约用同一
口径，真实 HTTP 边界由各宿主的端到端用例覆盖。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from muad_agent_core.agent import AgentRunStatus, SuspensionKind
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.model import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)
from muad_contracts import OperationWatermark


def text_reply(text: str = "完成") -> ModelResponse:
    return ModelResponse(content=text, finish_reason="stop")


def tool_call_reply(*calls: ModelToolCall) -> ModelResponse:
    return ModelResponse(content="", finish_reason="tool_calls", tool_calls=tuple(calls))


class ScriptedModelProvider(ModelProvider):
    """按脚本顺序作答，并记录每次请求（模型调用次数是"有没有多跑一轮"的证据）。"""

    def __init__(self, script: Sequence[ModelResponse]) -> None:
        self._script = list(script)
        self.requests: list[ModelRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("unexpected model call")
        return self._script.pop(0)


@dataclass(frozen=True, slots=True)
class RoundPlan:
    """一次执行请求：脚本、工具预算与是否已请求取消。"""

    script: tuple[ModelResponse, ...]
    max_tool_calls: int | None = None
    cancel: bool = False


@dataclass
class Observation:
    """一次真实适配器执行后**宿主中立**的观察结果。"""

    status: AgentRunStatus | None = None
    final_text: str = ""
    #: 真正执行过的工具 call_id（模型声明了但被拒/被预算挡下的不算）。
    executed: list[str] = field(default_factory=list)
    #: `(call_id, 工具名, 状态, 正文)`：工具结果是否如实体现在回合收口上。
    tool_results: list[tuple[str, str, str, str]] = field(default_factory=list)
    #: Kernel 经 `ON_INTERRUPT` 钩子交回的挂起事件（kind/prompt 由适配器声明）。
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    #: 适配器 `suspend()` 收到的 checkpoint（真实落库前的那一份）。
    checkpoints: list[RunnerCheckpoint] = field(default_factory=list)
    model_calls: int = 0
    cancelled: bool = False
    #: 结构边界顺序：assistant → tool_started → tool_completed → suspend。
    order: list[str] = field(default_factory=list)


class HostAdapter(Protocol):
    """一个真实宿主适配器：Runtime（记忆确认）或 Worker（异步工具）。"""

    label: str
    kind: SuspensionKind
    reason: str
    prompt: str
    expected_status: AgentRunStatus
    pending: tuple[OperationWatermark, ...]

    def staging_call(self, call_id: str = "call-1") -> ModelToolCall:
        """宿主自己的"会触发挂起"的工具调用。"""
        ...

    async def run(self, plan: RoundPlan) -> Observation:
        """用宿主真实端口跑一个执行请求（该挂起就落库）。"""
        ...

    async def resume(self) -> Observation:
        """从宿主自己的 checkpoint store 恢复同一个执行，不许重跑已核实的工具。"""
        ...


# ---------------------------------------------------------------------------
# 契约用例：宿主中立、可绑定任意 HostAdapter
# ---------------------------------------------------------------------------


async def suspension_lands_on_the_complete_round_boundary(adapter: HostAdapter) -> None:
    """挂起只发生在完整模型回合收口之后：工具跑完才停，停了就不再发起模型回合。"""
    observation = await adapter.run(
        RoundPlan((tool_call_reply(adapter.staging_call()), text_reply("不该被调用")))
    )
    assert observation.status is adapter.expected_status, observation.status
    assert observation.executed == ["call-1"]
    assert observation.model_calls == 1, "挂起之后不得再发起模型回合"
    assert observation.order == ["assistant", "tool_started", "tool_completed", "suspend"]
    assert len(observation.checkpoints) == 1
    checkpoint = observation.checkpoints[0]
    assert (checkpoint.turns, checkpoint.tool_calls) == (1, 1)
    assert checkpoint.pending_tool_calls == adapter.pending


async def interrupt_carries_the_adapter_reason(adapter: HostAdapter) -> None:
    """中断事件只反映适配器声明的分类：Kernel 不解释、也不改写成别的宿主分类。"""
    observation = await adapter.run(RoundPlan((tool_call_reply(adapter.staging_call()),)))
    assert [event["kind"] for event in observation.interrupts] == [adapter.reason]
    assert observation.interrupts[0]["prompt"] == adapter.prompt


async def unauthorized_tool_is_reported_and_not_executed(adapter: HostAdapter) -> None:
    """越权/未知工具不产生外部执行，但失败必须作为工具结果回到回合里。"""
    observation = await adapter.run(
        RoundPlan((tool_call_reply(ModelToolCall("call-1", "missing", {})), text_reply("收口")))
    )
    assert observation.executed == []
    assert observation.status is AgentRunStatus.COMPLETED
    assert observation.model_calls == 2
    assert [entry[1] for entry in observation.tool_results] == ["missing"]
    assert "unknown tool" in observation.tool_results[0][3]


async def tool_budget_is_enforced_before_suspension(adapter: HostAdapter) -> None:
    """工具预算是硬约束：超出预算的调用不执行，剩下的回合仍按正常收口（含挂起）。"""
    plan = RoundPlan(
        (
            tool_call_reply(
                adapter.staging_call("call-1"), ModelToolCall("call-2", "echo", {})
            ),
        ),
        max_tool_calls=1,
    )
    observation = await adapter.run(plan)
    assert observation.executed == ["call-1"]
    assert observation.status is adapter.expected_status
    assert observation.checkpoints[0].tool_calls == 1
    assert observation.model_calls == 1


async def cancellation_wins_over_suspension(adapter: HostAdapter) -> None:
    """取消先于挂起：还没开始就不该有任何工具执行或挂起落库。"""
    observation = await adapter.run(RoundPlan((tool_call_reply(adapter.staging_call()),), cancel=True))
    assert observation.cancelled is True
    assert observation.executed == []
    assert observation.checkpoints == []
    assert observation.interrupts == []


async def resume_uses_the_persisted_checkpoint_without_replaying_tools(adapter: HostAdapter) -> None:
    """恢复 = 同一循环 + 宿主自己落库的 checkpoint：已核实的工具不重跑。"""
    observation = await adapter.resume()
    assert observation.status is AgentRunStatus.COMPLETED
    assert observation.executed == []
    assert observation.model_calls == 1


#: 契约用例清单：每个宿主测试把它们逐条绑定到真实适配器上。
CONTRACT_CASES = (
    suspension_lands_on_the_complete_round_boundary,
    interrupt_carries_the_adapter_reason,
    unauthorized_tool_is_reported_and_not_executed,
    tool_budget_is_enforced_before_suspension,
    cancellation_wins_over_suspension,
    resume_uses_the_persisted_checkpoint_without_replaying_tools,
)


__all__ = [
    "CONTRACT_CASES",
    "HostAdapter",
    "Observation",
    "RoundPlan",
    "ScriptedModelProvider",
    "text_reply",
    "tool_call_reply",
]
