"""v2 tool events, prepared facts and deterministic batch stop."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from muad_agent_core.agent import AgentPolicy, AgentRunner, AgentRunRequest
from muad_agent_core.model import (
    ModelMessage,
    ModelResponse,
    ModelRole,
    ModelToolCall,
)
from muad_agent_core.tools import (
    ArtifactRef,
    ArtifactRole,
    AssistantTurn,
    PreparedCall,
    ToolCall,
    ToolCompleted,
    ToolDefinition,
    ToolEffect,
    ToolError,
    ToolRegistry,
    ToolResult,
    completed_from_result,
)
from muad_contracts import (
    TOOL_EVENT_SCHEMA_VERSION,
    PreparedCallEvent,
    ToolCompletedEvent,
)


class _Provider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> ModelResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        model_id="probe",
        instructions="test",
        messages=(ModelMessage(role=ModelRole.USER, content="go"),),
        policy=AgentPolicy(),
    )


def _tool_call(call_id: str, name: str, arguments: Mapping[str, Any] | None = None) -> ModelResponse:
    return ModelResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=(ModelToolCall(id=call_id, name=name, arguments=dict(arguments or {})),),
    )


def _text(content: str) -> ModelResponse:
    return ModelResponse(content=content, finish_reason="stop")


def _registry(handler: Any, name: str = "mcp::server::lookup") -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=name,
            description="lookup",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )
    return registry


async def test_assistant_prepared_and_completed_events_carry_identity() -> None:
    model_name = "mcp__server__lookup"
    turns: list[AssistantTurn] = []
    prepared: list[PreparedCall] = []
    completed: list[ToolCompleted] = []

    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        return ToolResult("pong")

    async def on_assistant(turn: AssistantTurn) -> None:
        turns.append(turn)

    async def on_prepared(item: PreparedCall) -> None:
        prepared.append(item)

    async def on_completed(item: ToolCompleted) -> None:
        completed.append(item)

    provider = _Provider(
        [
            _tool_call("c1", model_name, {"query": "x", "api_key": "sk-secret"}),
            _text("done"),
        ]
    )
    result = await AgentRunner(provider=provider, registry=_registry(handler)).run(
        _request(),
        on_assistant_turn=on_assistant,
        on_tool_prepared=on_prepared,
        on_tool_completed=on_completed,
    )

    assert result.final_text == "done"
    tool_turn = next(turn for turn in turns if turn.calls)
    assert tool_turn.message.tool_calls[0].name == model_name, "provider history keeps model name"
    assert tool_turn.calls[0].name == "mcp::server::lookup", "registry name is the internal identity"
    assert tool_turn.calls[0].model_name == model_name
    assert prepared[0].call == tool_turn.calls[0]
    assert prepared[0].source == "MCP"
    assert prepared[0].tool_name == "lookup"
    assert prepared[0].args_hash.startswith("sha256:")
    assert "sk-secret" not in repr(prepared[0].args_preview)
    assert completed[0].tool_call_id == "c1"
    assert completed[0].status == "OK"
    assert completed[0].content == "pong"

    prepared_event = PreparedCallEvent.model_validate(
        {
            "tool_call_id": prepared[0].call.call_id,
            "name": prepared[0].call.name,
            "model_name": prepared[0].call.model_name,
            "args_hash": prepared[0].args_hash,
            "args_preview": dict(prepared[0].args_preview),
            "source": prepared[0].source,
            "skill_id": None,
            "skill_key": None,
            "tool_name": prepared[0].tool_name,
            "execution_mode": None,
            "retry_safety": str(prepared[0].retry_safety),
        }
    )
    assert prepared_event.schema_version == TOOL_EVENT_SCHEMA_VERSION == 2
    assert prepared_event.state == "STARTED"

    completed_event = ToolCompletedEvent.model_validate(
        {
            "tool_call_id": completed[0].tool_call_id,
            "tool_name": completed[0].tool_name,
            "status": completed[0].status,
            "outcome": {
                "business_status": str(completed[0].outcome.business_status),
                "failure_layer": str(completed[0].outcome.failure_layer),
                "error_code": completed[0].outcome.error_code,
                "retryable": completed[0].outcome.retryable,
                "evidence_preview": completed[0].outcome.evidence.preview,
                "exit_code": completed[0].outcome.exit_code,
            },
            "content": completed[0].content,
        }
    )
    assert completed_event.schema_version == 2
    assert completed_event.state == "COMPLETED"
    assert completed_event.outcome.boundary_kind == "NONE"
    assert completed_event.outcome.stop_confirmed is True


async def test_prepared_event_write_failure_aborts_handler_io() -> None:
    calls: list[str] = []
    completed: list[ToolCompleted] = []

    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        calls.append(call.call_id)
        return ToolResult("should not run")

    async def fail_prepared(item: PreparedCall) -> None:
        raise RuntimeError("event store unavailable")

    async def on_completed(item: ToolCompleted) -> None:
        completed.append(item)

    provider = _Provider(
        [_tool_call("c1", "mcp__server__lookup"), _text("recovered")]
    )
    result = await AgentRunner(provider=provider, registry=_registry(handler)).run(
        _request(),
        on_tool_prepared=fail_prepared,
        on_tool_completed=on_completed,
    )

    assert calls == [], "no prepared fact means no external execution"
    assert result.final_text == "recovered"
    assert completed[0].content.startswith("tool failed:")
    assert "event store unavailable" in completed[0].content


async def test_deterministic_read_failure_allows_an_independent_serial_call() -> None:
    calls: list[str] = []
    completed: list[ToolCompleted] = []

    async def failing(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        calls.append("fail")
        raise ToolError("PLATFORM_AUTH_REJECTED", "rejected", retryable=False)

    async def should_not_run(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        calls.append("second")
        return ToolResult("ran")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fail",
            description="fail",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=failing,
        )
    )
    registry.register(
        ToolDefinition(
            name="second",
            description="second",
            input_schema={"type": "object"},
            effect=ToolEffect.READ,
            handler=should_not_run,
        )
    )
    provider = _Provider(
        [
            ModelResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=(
                    ModelToolCall("c1", "fail", {}),
                    ModelToolCall("c2", "second", {}),
                ),
            ),
            ModelResponse(content="Completed the second read.", finish_reason="stop"),
        ]
    )

    async def on_completed(item: ToolCompleted) -> None:
        completed.append(item)

    result = await AgentRunner(provider=provider, registry=registry).run(
        _request(), on_tool_completed=on_completed
    )

    assert result.status.value == "COMPLETED"
    assert calls == ["fail", "second"]
    assert len(completed) == 2
    assert completed[1].tool_call_id == "c2"
    assert completed[1].content == "ran"
    assert completed[1].outcome.retryable is False


async def test_typed_round_port_rewrites_result_and_completed_event_keeps_artifacts() -> None:
    from dataclasses import replace

    from muad_agent_core.model import ImagePart
    from muad_agent_core.tools import ArtifactRef

    seen: list[ToolResult] = []
    completed: list[ToolCompleted] = []

    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        return ToolResult(
            "body",
            artifacts=(ArtifactRef("artifact-1", "text/plain", filename="r.txt"),),
            follow_ups=(
                ModelMessage(
                    role=ModelRole.USER,
                    content=(
                        "[image]",
                        ImagePart(
                            media_type="image/png",
                            data_base64="AAAA",
                            artifact_id="artifact-1",
                        ),
                    ),
                ),
            ),
        )

    class _Port:
        async def finish_round(self, results: Any) -> Any:
            seen.extend(results)
            return tuple(replace(result, content="ref") for result in results)

    async def on_completed(item: ToolCompleted) -> None:
        completed.append(item)

    provider = _Provider([_tool_call("c1", "mcp__server__lookup"), _text("done")])
    await AgentRunner(
        provider=provider,
        registry=_registry(handler),
        tool_round_results=_Port(),
    ).run(_request(), on_tool_completed=on_completed)

    assert seen[0].content == "body"
    assert completed[0].content == "ref"
    assert completed[0].artifacts[0].artifact_id == "artifact-1"
    assert completed[0].follow_ups[0].kind == "image"
    assert completed[0].follow_ups[0].media_type == "image/png"
    assert completed[0].follow_ups[0].artifact_id == "artifact-1"


def test_image_artifact_is_marked_as_image_in_completed_events() -> None:
    call = ToolCall("call-1", "view_image", "view_image", {})
    result = ToolResult(
        "image seen",
        artifacts=(ArtifactRef("artifact-1", "image/png", filename="x.png", size=3),),
    )

    completed = completed_from_result(call, result, "SUCCEEDED")

    assert completed.artifacts[0].role is ArtifactRole.IMAGE
