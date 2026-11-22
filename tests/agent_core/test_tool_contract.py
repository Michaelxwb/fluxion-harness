"""Typed tool contract: handlers declare results; the pipeline never sniffs content."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from muad_agent_core.tools import (
    ArtifactRef,
    BusinessStatus,
    FailureLayer,
    ToolCall,
    ToolDefinition,
    ToolEffect,
    ToolError,
    ToolExecutionPipeline,
    ToolRegistry,
    ToolResult,
)


def _definition(handler: Any, *, name: str = "echo") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
        handler=handler,
    )


async def _execute(handler: Any, arguments: Mapping[str, Any] | None = None):
    registry = ToolRegistry()
    registry.register(_definition(handler))
    pipeline = ToolExecutionPipeline(registry=registry)
    return await pipeline.execute(
        pipeline.prepare(call_id="call-1", tool_name="echo", arguments=arguments or {})
    )


async def test_handler_result_carries_call_identity_and_artifacts() -> None:
    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        assert call.name == "echo"
        assert call.model_name == "echo"
        return ToolResult(
            "ok",
            follow_ups=(),
            artifacts=(ArtifactRef("artifact-1", "image/png", filename="x.png"),),
        )

    execution = await _execute(handler)

    assert execution.status == "SUCCEEDED"
    assert execution.call == ToolCall(
        call_id="call-1", name="echo", model_name="echo", arguments={}
    )
    assert execution.result.call_id == "call-1"
    assert execution.result.outcome.call_id == "call-1"
    assert execution.result.outcome.tool_name == "echo"
    assert execution.result.artifacts[0].artifact_id == "artifact-1"


@pytest.mark.parametrize(
    "content",
    [
        "[1,2,3]",
        "null",
        "42",
        '"ok"',
        '{"error": "data"}',
    ],
)
async def test_success_content_is_data_even_when_it_looks_like_an_error(content: str) -> None:
    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        return ToolResult(content)

    execution = await _execute(handler)

    assert execution.status == "SUCCEEDED"
    assert execution.result.content == content
    assert execution.result.outcome.business_status is BusinessStatus.NOT_APPLICABLE
    assert execution.result.outcome.is_failure is False


async def test_typed_tool_error_is_not_reclassified_from_its_message() -> None:
    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        raise ToolError(
            "PLATFORM_AUTH_REJECTED",
            "signature invalid",
            detail={"http_status": 401},
        )

    execution = await _execute(handler)

    assert execution.status == "FAILED"
    outcome = execution.result.outcome
    assert outcome.business_status is BusinessStatus.FAILED
    assert outcome.failure_layer is FailureLayer.TOOL
    assert outcome.error_code == "PLATFORM_AUTH_REJECTED"
    assert outcome.retryable is False
    payload = json.loads(execution.result.content)
    assert payload["error"]["code"] == "PLATFORM_AUTH_REJECTED"
    assert payload["error"]["detail"] == {"http_status": 401}


async def test_retryable_tool_error_keeps_the_declared_flag() -> None:
    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        raise ToolError("UPSTREAM_TIMEOUT", "slow", retryable=True)

    execution = await _execute(handler)

    assert execution.result.outcome.retryable is True
    assert json.loads(execution.result.content)["error"]["retryable"] is True


async def test_string_handler_return_is_a_contract_error() -> None:
    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> str:
        return "legacy"

    with pytest.raises(TypeError, match="expected ToolResult"):
        await _execute(handler)


async def test_pipeline_failures_are_typed() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="strict",
            description="strict",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            effect=ToolEffect.READ,
            handler=None,
        )
    )
    pipeline = ToolExecutionPipeline(registry=registry)

    schema = await pipeline.execute(
        pipeline.prepare(call_id="s1", tool_name="strict", arguments={"value": "x"})
    )
    assert schema.status == "FAILED"
    assert schema.result.outcome.error_code == "SCHEMA_INVALID"
    assert schema.result.outcome.retryable is True

    missing = await pipeline.execute(
        pipeline.prepare(call_id="s2", tool_name="ghost", arguments={})
    )
    assert missing.status == "NOT_FOUND"
    assert missing.result.outcome.error_code == "TOOL_NOT_FOUND"
    assert missing.result.outcome.retryable is True


async def test_externalizable_result_false_is_propagated_to_the_result() -> None:
    async def handler(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        return ToolResult("full text that must stay inline")

    definition = ToolDefinition(
        name="read_attachment",
        description="read",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
        handler=handler,
        externalizable_result=False,
    )
    registry = ToolRegistry()
    registry.register(definition)
    pipeline = ToolExecutionPipeline(registry=registry)
    execution = await pipeline.execute(
        pipeline.prepare(call_id="call-1", tool_name="read_attachment", arguments={})
    )

    assert execution.result.externalizable is False
