"""`ExecutionOutcome` 分类矩阵（design §4.2 / §8 单元）。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from muad_agent_core.tools import (
    BusinessStatus,
    FailureLayer,
    ToolDefinition,
    ToolEffect,
    ToolExecutionPipeline,
    ToolRegistry,
    skill_execution_outcome,
)


def _skill_payload(
    status: str,
    *,
    stdout: str = "",
    stderr: str = "",
    result: Any = None,
    exit_code: int | None = 0,
    output_truncated: bool = False,
) -> dict[str, Any]:
    """与 `skill_tools._execution_payload` 同形的 Skill 执行载荷。"""
    return {
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "duration_ms": 1,
        "exit_code": exit_code,
        "outputs": [],
        "output_notice": None,
        "output_truncated": output_truncated,
    }


def test_skill_success_with_structured_result_is_confirmed() -> None:
    outcome = skill_execution_outcome(_skill_payload("SUCCEEDED", result={"answer": 42}))
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.SUCCEEDED
    assert outcome.failure_layer is FailureLayer.NONE
    assert outcome.is_failure is False
    assert outcome.evidence.preview == "", "成功调用不落正文预览"


def test_skill_success_without_structured_result_is_unknown() -> None:
    outcome = skill_execution_outcome(_skill_payload("SUCCEEDED", stdout="plain text"))
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.UNKNOWN
    assert outcome.is_failure is False


def test_skill_success_with_error_result_is_unknown_not_invented_failure() -> None:
    outcome = skill_execution_outcome(
        _skill_payload("SUCCEEDED", result={"ok": False, "error": "bad data"})
    )
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.UNKNOWN
    assert outcome.is_failure is False


def test_skill_failure_envelope_carries_code_and_retryable() -> None:
    envelope = json.dumps(
        {
            "ok": False,
            "code": "UPSTREAM_5XX",
            "message": "upstream returned 502",
            "retryable": True,
        }
    )
    outcome = skill_execution_outcome(
        _skill_payload("FAILED", stderr=f"log line\n{envelope}", exit_code=3)
    )
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.FAILED
    assert outcome.failure_layer is FailureLayer.CONTENT
    assert outcome.error_code == "UPSTREAM_5XX"
    assert outcome.error_message == "upstream returned 502"
    assert outcome.exit_code == 3
    assert outcome.retryable is True
    assert outcome.is_failure is True


def test_skill_failure_without_envelope_is_deterministic() -> None:
    outcome = skill_execution_outcome(_skill_payload("FAILED", stderr="boom", exit_code=1))
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.FAILED
    assert outcome.failure_layer is FailureLayer.CONTENT
    assert outcome.error_code == "SKILL_EXECUTION_FAILED"
    assert outcome.retryable is False


def test_skill_timeout_is_retryable_content_failure() -> None:
    outcome = skill_execution_outcome(_skill_payload("TIMED_OUT", stderr="too slow"))
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.TIMED_OUT
    assert outcome.failure_layer is FailureLayer.CONTENT
    assert outcome.retryable is True
    assert outcome.is_failure is True


def test_skill_cancelled_is_not_a_failure() -> None:
    outcome = skill_execution_outcome(_skill_payload("CANCELLED", stderr="stopped"))
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.CANCELLED
    assert outcome.failure_layer is FailureLayer.NONE
    assert outcome.is_failure is False


def test_workspace_and_draft_payloads_are_not_business_executions() -> None:
    """退出码只是结果证据：没有 Skill 执行载荷就没有 CONTENT 失败。"""
    workspace = {"stdout": "", "stderr": "grep: no match", "exit_code": 1, "changed_files": []}
    draft = {"stdout": "", "stderr": "boom", "exit_code": 1, "revision": 2}
    assert skill_execution_outcome(workspace) is None
    assert skill_execution_outcome(draft) is None


def test_tool_failure_envelope_keeps_output_truncated() -> None:
    from muad_agent_core.tools import tool_failure_outcome

    outcome = tool_failure_outcome(
        json.dumps(
            {
                "error": {"code": "SANDBOX_OUTPUT_LIMIT", "message": "output limit exceeded"},
                "output_truncated": True,
            }
        )
    )
    assert outcome is not None
    assert outcome.failure_layer is FailureLayer.TOOL
    assert outcome.evidence.output_truncated is True


def test_evidence_preview_is_redacted_and_truncation_is_visible() -> None:
    secret = "token=super-secret-value"
    outcome = skill_execution_outcome(
        _skill_payload(
            "FAILED",
            stderr=f"request failed {secret}",
            exit_code=1,
            output_truncated=True,
        )
    )
    assert outcome is not None
    assert "super-secret-value" not in outcome.evidence.preview
    assert "***" in outcome.evidence.preview
    assert outcome.evidence.output_truncated is True


async def _run_pipeline(definition: ToolDefinition) -> Any:
    registry = ToolRegistry()
    registry.register(definition)
    pipeline = ToolExecutionPipeline(registry=registry)
    prepared = pipeline.prepare(call_id="c1", tool_name=definition.name, arguments={})
    return await pipeline.execute(prepared)


async def test_pipeline_calls_outcome_parser_once() -> None:
    calls: list[str] = []

    def parser(content: str) -> Any:
        calls.append(content)
        return skill_execution_outcome(json.loads(content))

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return json.dumps(_skill_payload("FAILED", stderr="boom", exit_code=1))

    result = await _run_pipeline(
        ToolDefinition(
            name="execute",
            description="skill",
            input_schema={"type": "object"},
            effect=ToolEffect.EXTERNAL,
            handler=handler,
            outcome_parser=parser,
        )
    )

    assert len(calls) == 1
    assert result.status == "SUCCEEDED", "transport OK；业务失败在 outcome 上"
    assert result.outcome is not None
    assert result.outcome.business_status is BusinessStatus.FAILED
    assert result.outcome.failure_layer is FailureLayer.CONTENT
    assert result.outcome.call_id == "c1"
    assert result.outcome.tool_name == "execute"


async def test_pipeline_parser_returning_none_marks_not_applicable() -> None:
    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return json.dumps({"stderr": "grep: no match", "exit_code": 1, "output_truncated": True})

    result = await _run_pipeline(
        ToolDefinition(
            name="execute",
            description="workspace shell",
            input_schema={"type": "object"},
            effect=ToolEffect.EXTERNAL,
            handler=handler,
            outcome_parser=skill_execution_outcome,
        )
    )

    assert result.status == "SUCCEEDED"
    assert result.outcome is not None
    assert result.outcome.business_status is BusinessStatus.NOT_APPLICABLE
    assert result.outcome.exit_code == 1
    assert "grep: no match" in result.outcome.evidence.preview
    assert result.outcome.evidence.output_truncated is True
    assert result.outcome.is_failure is False


async def test_pipeline_parser_crash_is_a_tool_failure_not_a_run_crash() -> None:
    def parser(content: str) -> Any:
        raise ValueError("broken parser")

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return "{}"

    result = await _run_pipeline(
        ToolDefinition(
            name="execute",
            description="skill",
            input_schema={"type": "object"},
            effect=ToolEffect.EXTERNAL,
            handler=handler,
            outcome_parser=parser,
        )
    )

    assert result.status == "FAILED"
    assert result.outcome is not None
    assert result.outcome.business_status is BusinessStatus.FAILED
    assert result.outcome.failure_layer is FailureLayer.TOOL
    assert result.outcome.error_code == "COMMON_INTERNAL_ERROR"
