"""终止快照/原因与失败跟踪（design §4.3 / TASK-009）。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    AgentRunStatus,
    RunnerCancelled,
    TerminationReason,
    TerminationSnapshot,
    decisive_failure,
    reason_for_error_code,
)
from muad_agent_core.agent.termination import FailureEvidence, FailureState, PendingToolCall
from muad_agent_core.model import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
)
from muad_agent_core.tools import (
    ToolDefinition,
    ToolEffect,
    ToolRegistry,
    skill_execution_outcome,
)


class _Provider:
    def __init__(self, script: list[ModelResponse]) -> None:
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
        tool_calls=tuple(
            ModelToolCall(id=call_id, name=name, arguments={}) for call_id, name in calls
        ),
    )


def _request(*, policy: AgentPolicy | None = None) -> AgentRunRequest:
    return AgentRunRequest(
        model_id="gpt-4o-mini",
        instructions="be helpful",
        messages=(ModelMessage(role=ModelRole.USER, content="hi"),),
        policy=policy or AgentPolicy(),
    )


def _shell_payload() -> str:
    return json.dumps({"stdout": "", "stderr": "grep: no match", "exit_code": 1})


def test_reason_for_error_code_maps_existing_codes() -> None:
    assert reason_for_error_code("RUN_NO_PROGRESS") is TerminationReason.NO_PROGRESS
    assert reason_for_error_code("RUN_DEADLINE_EXCEEDED") is TerminationReason.DEADLINE
    assert reason_for_error_code("CANCELLED") is TerminationReason.CANCELLED
    assert reason_for_error_code("RUN_BUDGET_EXCEEDED") is TerminationReason.TURN_BUDGET
    assert reason_for_error_code("MODEL_UNAVAILABLE") is TerminationReason.RUN_ERROR
    assert reason_for_error_code(None) is TerminationReason.RUN_ERROR


def test_snapshot_payload_round_trip() -> None:
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        turns_used=3,
        turns_limit=20,
        tool_calls_used=4,
        tool_calls_limit=30,
        elapsed_ms=1234,
        deadline_ms=60000,
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="execute",
                code="PLATFORM_AUTH_REJECTED",
                message="rejected",
                failure_layer="CONTENT",
                exit_code=3,
                retryable=False,
                evidence_preview="mssw session rejected",
            ),
            identical_failures={"execute:PLATFORM_AUTH_REJECTED:CONTENT": 1},
            no_progress=True,
        ),
        pending_tool_calls=(PendingToolCall(call_id="c9", tool_name="execute"),),
    )

    restored = TerminationSnapshot.from_payload(snapshot.as_payload())

    assert restored == snapshot
    assert restored is not None and restored.reason is TerminationReason.NO_PROGRESS


def test_decisive_failure_only_when_no_progress() -> None:
    failure = FailureEvidence(tool="execute", code="X", failure_layer="CONTENT")
    snapshot = TerminationSnapshot(
        status="NO_PROGRESS",
        failure_state=FailureState(last_failure=failure, no_progress=True),
    )

    assert decisive_failure(snapshot, TerminationReason.NO_PROGRESS) is failure
    for reason in (
        TerminationReason.TURN_BUDGET,
        TerminationReason.TOOL_BUDGET,
        TerminationReason.DEADLINE,
        TerminationReason.CANCELLED,
        TerminationReason.RUN_ERROR,
    ):
        assert decisive_failure(snapshot, reason) is None
    assert decisive_failure(None, TerminationReason.NO_PROGRESS) is None


async def test_turn_budget_snapshot_derives_type_and_pending_calls() -> None:
    provider = _Provider([_tool_call(("c1", "echo"))])
    registry = ToolRegistry()

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return json.dumps({"ok": True})

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )

    result = await AgentRunner(provider=provider, registry=registry).run(
        _request(policy=AgentPolicy(max_turns=1))
    )

    assert result.status is AgentRunStatus.BUDGET_EXCEEDED
    snapshot = result.termination
    assert snapshot is not None
    assert snapshot.budget_kind.value == "TURN"
    assert snapshot.turns_used == 1 and snapshot.turns_limit == 1
    assert [call.tool_name for call in snapshot.pending_tool_calls] == ["echo"]
    assert snapshot.failure_state.last_failure is None


async def test_tool_budget_snapshot_derives_type_and_pending_calls() -> None:
    provider = _Provider([_tool_call(("c1", "echo"), ("c2", "echo"))])
    registry = ToolRegistry()
    calls: list[str] = []

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        calls.append(call_id)
        return json.dumps({"ok": True})

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )

    result = await AgentRunner(provider=provider, registry=registry).run(
        _request(policy=AgentPolicy(max_tool_calls=1))
    )

    assert calls == ["c1"], "预算外的调用不执行"
    assert result.status is AgentRunStatus.BUDGET_EXCEEDED
    snapshot = result.termination
    assert snapshot is not None
    assert snapshot.budget_kind.value == "TOOL"
    assert snapshot.tool_calls_used == 1
    assert [call.call_id for call in snapshot.pending_tool_calls] == ["c2"]
    assert snapshot.failure_state.last_failure is None


async def test_runner_cancellation_carries_a_snapshot() -> None:
    provider = _Provider([])
    registry = ToolRegistry()

    runner = AgentRunner(provider=provider, registry=registry)
    with pytest.raises(RunnerCancelled) as caught:
        await runner.run(_request(), is_cancelled=lambda: True)

    snapshot = caught.value.termination
    assert snapshot is not None
    assert snapshot.turns_used == 0
    assert snapshot.pending_tool_calls == ()
    assert snapshot.failure_state.no_progress is False


async def test_workspace_shell_nonzero_exit_is_evidence_not_no_progress() -> None:
    provider = _Provider([_tool_call(("c1", "workspace_shell")), _text("done")])
    registry = ToolRegistry()

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return _shell_payload()

    registry.register(
        ToolDefinition(
            name="workspace_shell",
            description="diagnostic command",
            input_schema={"type": "object"},
            effect=ToolEffect.EXTERNAL,
            handler=handler,
        )
    )

    result = await AgentRunner(provider=provider, registry=registry).run(_request())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_text == "done"
    assert result.termination is not None
    assert result.termination.failure_state.no_progress is False
    assert result.termination.failure_state.last_failure is None


async def test_skill_content_failure_stops_on_first_occurrence() -> None:
    payload = json.dumps(
        {
            "status": "FAILED",
            "stdout": "",
            "stderr": json.dumps(
                {"ok": False, "code": "PLATFORM_AUTH_REJECTED", "message": "rejected"}
            ),
            "result": None,
            "exit_code": 3,
            "outputs": [],
            "output_notice": None,
            "output_truncated": False,
        }
    )
    provider = _Provider([_tool_call(("c1", "execute"))])
    registry = ToolRegistry()

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return payload

    registry.register(
        ToolDefinition(
            name="execute",
            description="skill",
            input_schema={"type": "object"},
            effect=ToolEffect.EXTERNAL,
            handler=handler,
            outcome_parser=skill_execution_outcome,
        )
    )

    result = await AgentRunner(provider=provider, registry=registry).run(_request())

    assert result.status is AgentRunStatus.NO_PROGRESS
    assert len(provider.requests) == 1
    snapshot = result.termination
    assert snapshot is not None
    assert snapshot.reason is TerminationReason.NO_PROGRESS
    failure = snapshot.failure_state.last_failure
    assert failure is not None
    assert failure.failure_layer == "CONTENT"
    assert failure.code == "PLATFORM_AUTH_REJECTED"
    assert failure.exit_code == 3
    assert decisive_failure(snapshot, TerminationReason.NO_PROGRESS) is failure
