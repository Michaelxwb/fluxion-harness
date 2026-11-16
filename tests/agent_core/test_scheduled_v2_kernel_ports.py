"""TASK-009：Kernel 的挂起/恢复/事件端口是**中立**的，两份适配器跑同一组契约。

不得 Mock 的真实边界：本文件是纯逻辑（`tests/agent_core/conftest.py` 标 `no_datastore`），
被测对象是真实的 `AgentRunner` 循环与真实的中立类型；两个"适配器"只是同一组端口的两份内存
实现——Runtime 形状（整回合人类确认）与 Worker 形状（工具级异步操作）——用来证明 Kernel
不按宿主分支、也不会把每一种挂起都解释成 Memory。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    AgentRunStatus,
    RunnerCancelled,
    SuspensionKind,
    SuspensionRequest,
    SuspensionResume,
    resume_checkpoint,
)
from muad_agent_core.agent.continuation import (
    ExecutionEventBatch,
    RunnerCheckpoint,
    WaitDecision,
)
from muad_agent_core.context.compactor import ContextCompactor
from muad_agent_core.hooks import HookEvent, HookPipeline
from muad_agent_core.model import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_core.tools.round_results import ToolResultRoundPort
from muad_contracts import OperationStatus, OperationWatermark

# ---------------------------------------------------------------------------
# 共享假件（真实循环 + 真实的端口形状，不含数据库）
# ---------------------------------------------------------------------------


class ScriptedModelProvider(ModelProvider):
    def __init__(self, script: Sequence[ModelResponse]) -> None:
        self._script = list(script)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("unexpected model call")
        return self._script.pop(0)


def _text(content: str) -> ModelResponse:
    return ModelResponse(content=content, finish_reason="stop")


def _tool_call(*calls: tuple[str, str]) -> ModelResponse:
    return ModelResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=tuple(ModelToolCall(id=call_id, name=name) for call_id, name in calls),
    )


def _registry(calls: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        calls.append(call_id)
        return json.dumps({"echo": call_id})

    registry.register(
        ToolDefinition(
            name="echo",
            description="echoes the call id",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )
    return registry


@dataclass
class StagedSuspension:
    """一份适配器的挂起实现：暂存请求，并在 `suspend()` 时把 checkpoint 交回来。"""

    request: SuspensionRequest | None = None
    checkpoints: list[RunnerCheckpoint] = field(default_factory=list)

    @property
    def suspend_request(self) -> SuspensionRequest | None:
        return self.request

    async def suspend(self, progress: RunnerCheckpoint) -> None:
        self.checkpoints.append(progress)


@dataclass
class RecordingEvents:
    """事件/检查点端口的宿主形状：只记录，不落库。"""

    label: str
    checkpoints: list[RunnerCheckpoint] = field(default_factory=list)
    drains: int = 0

    async def drain_ready(self, cursor: int, limit: int) -> ExecutionEventBatch:
        self.drains += 1
        return ExecutionEventBatch((), cursor)

    async def checkpoint(self, checkpoint: RunnerCheckpoint) -> None:
        self.checkpoints.append(checkpoint)

    async def try_wait(self, checkpoint: RunnerCheckpoint, *, assistant_text: str) -> WaitDecision:
        return WaitDecision.CONTINUE


def _watermark(call_id: str, status: OperationStatus = OperationStatus.SUBMITTED) -> OperationWatermark:
    return OperationWatermark(operation_id=uuid4(), call_id=call_id, status=status)


def _request(
    kind: SuspensionKind, reason: str, pending: tuple[OperationWatermark, ...] = ()
) -> SuspensionRequest:
    return SuspensionRequest(
        interrupt_id=uuid4(),
        reason=reason,
        prompt=f"回答 {reason}",
        text_prompt=f"文本回答 {reason}",
        kind=kind,
        pending_tool_calls=pending,
    )


@dataclass(frozen=True)
class AdapterHarness:
    """同一组端口的两种宿主形状；差别只在适配器，不在 Kernel。"""

    label: str
    kind: SuspensionKind
    reason: str
    pending: tuple[OperationWatermark, ...] = ()

    @property
    def expected_status(self) -> AgentRunStatus:
        return (
            AgentRunStatus.WAITING_TOOL
            if self.kind is SuspensionKind.TOOL_OPERATION
            else AgentRunStatus.WAITING_INPUT
        )


ADAPTERS = (
    pytest.param(AdapterHarness("runtime", SuspensionKind.HUMAN_INPUT, "MEMORY_CONFIRMATION"), id="runtime"),
    pytest.param(
        AdapterHarness(
            "worker",
            SuspensionKind.TOOL_OPERATION,
            "ASYNC_TOOL_OPERATION",
            (_watermark("call-1"), _watermark("call-2")),
        ),
        id="worker",
    ),
)


@dataclass
class Observation:
    status: AgentRunStatus
    final_text: str
    events: list[dict[str, Any]]
    executes: list[str]
    suspend_checkpoints: list[RunnerCheckpoint]
    order: list[str]


async def _observe_suspension(harness: AdapterHarness) -> Observation:
    """固定场景跑一遍：模型要一个工具 → 工具执行 → 回合收口时挂起。"""
    executes: list[str] = []
    suspension = StagedSuspension(_request(harness.kind, harness.reason, harness.pending))
    events: list[dict[str, Any]] = []
    order: list[str] = []
    hooks = HookPipeline()

    async def on_interrupt(context: Any) -> None:
        events.append(dict(context.payload))

    hooks.register(HookEvent.ON_INTERRUPT, on_interrupt)
    runner = AgentRunner(
        provider=ScriptedModelProvider([_tool_call(("call-1", "echo")), _text("done")]),
        registry=_registry(executes),
        hooks=hooks,
        tool_suspension=suspension,
    )

    async def assistant(message: ModelMessage) -> None:
        order.append("assistant")

    async def started(call_id: str, name: str) -> None:
        order.append("tool_started")

    async def completed(
        call_id: str, name: str, status: str, artifact_id: str | None, text: str | None
    ) -> None:
        order.append("tool_completed")

    result = await runner.run(
        AgentRunRequest(model_id="m", instructions="i"),
        on_assistant_turn=assistant,
        on_tool_started=started,
        on_tool_completed=completed,
    )
    return Observation(
        result.status,
        result.final_text,
        events,
        executes,
        suspension.checkpoints,
        order,
    )


# ---------------------------------------------------------------------------
# 中立请求/恢复载荷的形状
# ---------------------------------------------------------------------------


def test_suspension_request_rejects_a_mixed_shape() -> None:
    with pytest.raises(ValueError, match="TOOL_OPERATION"):
        _request(SuspensionKind.TOOL_OPERATION, "ASYNC")
    with pytest.raises(ValueError, match="HUMAN_INPUT"):
        _request(SuspensionKind.HUMAN_INPUT, "MEMORY", (_watermark("call-1"),))
    with pytest.raises(ValueError, match="call_id"):
        _request(
            SuspensionKind.TOOL_OPERATION,
            "ASYNC",
            (_watermark("call-1"), _watermark("call-1")),
        )
    with pytest.raises(ValueError, match="reason"):
        SuspensionRequest(interrupt_id=uuid4(), reason=" ", prompt="q")


def test_resume_checkpoint_only_drops_terminal_operations() -> None:
    """乱序结果只推进自己那一条；全部终态才让待定集合变空。"""
    first, second = _watermark("call-1"), _watermark("call-2")
    checkpoint = RunnerCheckpoint(3, 2, 10, 5, 9, (first, second))
    resume = SuspensionResume(
        uuid4(), SuspensionKind.TOOL_OPERATION, consumed=(_watermark("call-2", OperationStatus.RUNNING),)
    )
    partial = resume_checkpoint(checkpoint, resume)
    assert [watermark.call_id for watermark in partial.pending_tool_calls] == ["call-1", "call-2"]
    assert partial.pending_tool_calls[1].status is OperationStatus.RUNNING
    assert (partial.turns, partial.tool_calls, partial.consumed_event_seq) == (3, 2, 9)

    done = resume_checkpoint(
        checkpoint,
        SuspensionResume(
            uuid4(),
            SuspensionKind.TOOL_OPERATION,
            consumed=(
                _watermark("call-1", OperationStatus.COMPLETED),
                _watermark("call-2", OperationStatus.FAILED),
            ),
        ),
    )
    assert done.pending_tool_calls == ()


def test_resume_checkpoint_ignores_unknown_and_stale_watermarks() -> None:
    """迟到/重复结果不扩表；同一 call 多条取最大的 receipt_seq。"""
    checkpoint = RunnerCheckpoint(pending_tool_calls=(_watermark("call-1"),))
    late = resume_checkpoint(
        checkpoint, SuspensionResume(uuid4(), SuspensionKind.TOOL_OPERATION, consumed=(_watermark("call-9"),))
    )
    assert late.pending_tool_calls == checkpoint.pending_tool_calls

    newer = _watermark("call-1", OperationStatus.RUNNING)
    newer = OperationWatermark(
        operation_id=newer.operation_id, call_id="call-1", status=OperationStatus.RUNNING, receipt_seq=4
    )
    older = OperationWatermark(
        operation_id=newer.operation_id, call_id="call-1", status=OperationStatus.COMPLETED, receipt_seq=2
    )
    kept = resume_checkpoint(
        checkpoint,
        SuspensionResume(uuid4(), SuspensionKind.TOOL_OPERATION, consumed=(newer, older)),
    )
    assert kept.pending_tool_calls[0].receipt_seq == 4
    assert kept.pending_tool_calls[0].status is OperationStatus.RUNNING


def test_resume_checkpoint_without_pending_calls_is_identity() -> None:
    checkpoint = RunnerCheckpoint(1, 1, 2, 2, 3)
    assert resume_checkpoint(checkpoint, SuspensionResume(uuid4(), SuspensionKind.HUMAN_INPUT)) is checkpoint


# ---------------------------------------------------------------------------
# 两份适配器共用的行为契约
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_kernel_suspends_at_the_complete_round_boundary(harness: AdapterHarness) -> None:
    observation = await _observe_suspension(harness)
    assert observation.status is harness.expected_status
    assert observation.final_text == f"回答 {harness.reason}"
    # 整回合边界：工具跑完了才挂起，挂起后不再发起模型回合。
    assert observation.executes == ["call-1"]
    assert observation.order == ["assistant", "tool_started", "tool_completed"]


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_interrupt_event_carries_the_adapter_reason(harness: AdapterHarness) -> None:
    observation = await _observe_suspension(harness)
    assert len(observation.events) == 1
    assert observation.events[0]["kind"] == harness.reason
    assert observation.events[0]["prompt"] == f"回答 {harness.reason}"


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_checkpoint_shape_is_the_same_for_both_adapters(harness: AdapterHarness) -> None:
    observation = await _observe_suspension(harness)
    assert len(observation.suspend_checkpoints) == 1
    checkpoint = observation.suspend_checkpoints[0]
    assert (checkpoint.turns, checkpoint.tool_calls) == (1, 1)
    assert checkpoint.pending_tool_calls == harness.pending


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_human_suspension_is_not_reinterpreted_as_memory(harness: AdapterHarness) -> None:
    """只有适配器声明的 reason 能出现在中断事件上；Kernel 不写死任何宿主分类。"""
    observation = await _observe_suspension(harness)
    kinds = {event["kind"] for event in observation.events}
    if harness.kind is SuspensionKind.HUMAN_INPUT:
        assert "MEMORY_CONFIRMATION" == harness.reason
    else:
        assert kinds == {"ASYNC_TOOL_OPERATION"}


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_unauthorized_tool_is_reported_without_execution(harness: AdapterHarness) -> None:
    executes: list[str] = []
    suspension = StagedSuspension(_request(harness.kind, harness.reason, harness.pending))
    runner = AgentRunner(
        provider=ScriptedModelProvider([_tool_call(("call-1", "missing")), _text("done")]),
        registry=_registry(executes),
        tool_suspension=suspension,
    )
    reported: list[str | None] = []

    async def on_tool_completed(
        call_id: str, name: str, status: str, artifact_id: str | None, text: str | None
    ) -> None:
        reported.append(text)

    result = await runner.run(
        AgentRunRequest(model_id="m", instructions="i"), on_tool_completed=on_tool_completed
    )
    assert result.status is harness.expected_status
    assert executes == []
    assert reported and "unknown tool" in (reported[0] or "")


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_tool_call_budget_is_enforced_before_suspension(harness: AdapterHarness) -> None:
    executes: list[str] = []
    suspension = StagedSuspension(_request(harness.kind, harness.reason, harness.pending))
    runner = AgentRunner(
        provider=ScriptedModelProvider([_tool_call(("call-1", "echo"), ("call-2", "echo"))]),
        registry=_registry(executes),
        tool_suspension=suspension,
    )
    result = await runner.run(
        AgentRunRequest(model_id="m", instructions="i", policy=AgentPolicy(max_tool_calls=1))
    )
    assert executes == ["call-1"]
    assert result.status is harness.expected_status
    assert result.tool_calls == 1


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_cancellation_wins_over_suspension(harness: AdapterHarness) -> None:
    executes: list[str] = []
    suspension = StagedSuspension(_request(harness.kind, harness.reason, harness.pending))
    runner = AgentRunner(
        provider=ScriptedModelProvider([_tool_call(("call-1", "echo"))]),
        registry=_registry(executes),
        tool_suspension=suspension,
    )
    with pytest.raises(RunnerCancelled):
        await runner.run(AgentRunRequest(model_id="m", instructions="i"), is_cancelled=lambda: True)
    assert suspension.checkpoints == []


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_event_port_checkpoint_is_skipped_when_the_round_suspends(harness: AdapterHarness) -> None:
    """挂起与事件端口的 checkpoint 不双写：一次回合只落一份收口检查点。"""
    executes: list[str] = []
    events = RecordingEvents(harness.label)
    suspension = StagedSuspension(_request(harness.kind, harness.reason, harness.pending))
    runner = AgentRunner(
        provider=ScriptedModelProvider([_tool_call(("call-1", "echo"))]),
        registry=_registry(executes),
        tool_suspension=suspension,
        runtime_events=events,
    )
    await runner.run(AgentRunRequest(model_id="m", instructions="i"))
    assert len(suspension.checkpoints) == 1
    assert events.checkpoints == []


@pytest.mark.parametrize("harness", ADAPTERS)
async def test_resume_reuses_the_same_loop_and_does_not_replay_tools(harness: AdapterHarness) -> None:
    """恢复 = 同一 Kernel、带 checkpoint 与新历史；未核实的工具调用不会被重跑。"""
    executes: list[str] = []
    checkpoint = RunnerCheckpoint(1, 1, 1, 1, 0, harness.pending)
    history = (
        ModelMessage(role=ModelRole.USER, content="开始"),
        ModelMessage(
            role=ModelRole.ASSISTANT,
            content="",
            tool_calls=(ModelToolCall(id="call-1", name="echo"),),
        ),
        ModelMessage(role=ModelRole.TOOL, content='{"echo": "call-1"}', tool_call_id="call-1"),
    )
    runner = AgentRunner(
        provider=ScriptedModelProvider([_text("完成")]),
        registry=_registry(executes),
        tool_suspension=StagedSuspension(),
    )
    result = await runner.run(
        AgentRunRequest(model_id="m", instructions="i", messages=history), checkpoint=checkpoint
    )
    assert result.status is AgentRunStatus.COMPLETED
    assert result.turns == 2
    assert executes == []


# ---------------------------------------------------------------------------
# 降级：压缩/整轮外置失败不得让 Run 失败，但必须可见
# ---------------------------------------------------------------------------


class _BrokenCompactor(ContextCompactor):
    async def compact(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        raise RuntimeError("summary backend down")


class _BrokenRoundResults(ToolResultRoundPort):
    async def finish_round(self, results: Sequence[ModelMessage]) -> Sequence[ModelMessage]:
        raise RuntimeError("artifact store down")


async def test_failed_compaction_degrades_to_the_original_history(caplog: pytest.LogCaptureFixture) -> None:
    executes: list[str] = []
    provider = ScriptedModelProvider([_tool_call(("call-1", "echo")), _text("done")])
    runner = AgentRunner(
        provider=provider,
        registry=_registry(executes),
        context_compactor=_BrokenCompactor(),
    )
    with caplog.at_level(logging.WARNING, logger="muad_agent_core.agent.runner"):
        result = await runner.run(AgentRunRequest(model_id="m", instructions="i"))
    assert result.status is AgentRunStatus.COMPLETED
    # 原历史一字未动：第二次请求里仍有那条完整的工具结果。
    second = provider.requests[-1].messages
    assert any(message.role is ModelRole.TOOL and message.tool_call_id == "call-1" for message in second)
    assert any(record.message == "context_compaction_failed" for record in caplog.records)


async def test_failed_round_results_keep_inline_results(caplog: pytest.LogCaptureFixture) -> None:
    executes: list[str] = []
    provider = ScriptedModelProvider([_tool_call(("call-1", "echo")), _text("done")])
    completed: list[tuple[str, str | None]] = []

    async def on_tool_completed(
        call_id: str, name: str, status: str, artifact_id: str | None, text: str | None
    ) -> None:
        completed.append((artifact_id or "", text))

    runner = AgentRunner(
        provider=provider,
        registry=_registry(executes),
        tool_round_results=_BrokenRoundResults(),
    )
    with caplog.at_level(logging.WARNING, logger="muad_agent_core.agent.runner"):
        result = await runner.run(
            AgentRunRequest(model_id="m", instructions="i"), on_tool_completed=on_tool_completed
        )
    assert result.status is AgentRunStatus.COMPLETED
    assert completed == [("", '{"echo": "call-1"}')]
    assert any(record.message == "tool_round_results_failed" for record in caplog.records)


def test_checkpoint_state_is_json_native_with_pending_operations() -> None:
    """checkpoint 的可持久形态由 Kernel 给出：宿主直接写 JSON 列即可。"""
    checkpoint = RunnerCheckpoint(1, 2, 3, 4, 5, (_watermark("call-1"),))
    state = json.loads(json.dumps(checkpoint.as_state()))
    assert (state["turns"], state["tool_calls"], state["consumed_event_seq"]) == (1, 2, 5)
    assert state["pending_tool_calls"][0]["call_id"] == "call-1"
    assert state["pending_tool_calls"][0]["status"] == OperationStatus.SUBMITTED.value


def test_pending_tool_calls_must_be_unique_in_the_checkpoint() -> None:
    watermark = _watermark("call-1")
    with pytest.raises(ValueError, match="call_id"):
        RunnerCheckpoint(pending_tool_calls=(watermark, watermark))
