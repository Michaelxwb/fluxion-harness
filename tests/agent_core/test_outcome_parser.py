"""`ExecutionOutcome` classification matrix (design §4.2 / §8 unit)."""

from __future__ import annotations

import json
from typing import Any

from muad_agent_core.tools import (
    BusinessStatus,
    FailureLayer,
    ToolError,
    shell_outcome,
    skill_execution_outcome,
    skill_result_outcome,
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
    """The structured executor envelope consumed by the Worker task path."""
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


def test_skill_timeout_is_unknown_boundary_failure_not_safe_to_repeat() -> None:
    outcome = skill_execution_outcome(_skill_payload("TIMED_OUT", stderr="too slow"))
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.TIMED_OUT
    assert outcome.failure_layer is FailureLayer.CONTENT
    assert outcome.retryable is False
    assert outcome.control.boundary_kind.value == "TIME"
    assert outcome.control.outcome_known is False
    assert outcome.control.evidence_status.value == "PARTIAL"
    assert outcome.control.retry_disposition.value == "BLOCK"
    assert outcome.is_failure is True


def test_skill_output_truncation_is_partial_boundary_evidence() -> None:
    outcome = skill_execution_outcome(
        _skill_payload("SUCCEEDED", stdout='{"answer": 42}', result={"answer": 42}, output_truncated=True)
    )
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.SUCCEEDED
    assert outcome.control.boundary_kind.value == "OUTPUT"
    assert outcome.control.evidence_status.value == "PARTIAL"
    assert outcome.control.outcome_known is False


def test_skill_cancelled_is_not_a_failure() -> None:
    outcome = skill_execution_outcome(_skill_payload("CANCELLED", stderr="stopped"))
    assert outcome is not None
    assert outcome.business_status is BusinessStatus.CANCELLED
    assert outcome.failure_layer is FailureLayer.NONE
    assert outcome.is_failure is False


def test_workspace_and_draft_payloads_are_not_business_executions() -> None:
    workspace = {"stdout": "", "stderr": "grep: no match", "exit_code": 1, "changed_files": []}
    draft = {"stdout": "", "stderr": "boom", "exit_code": 1, "revision": 2}
    assert skill_execution_outcome(workspace) is None
    assert skill_execution_outcome(draft) is None


def test_shell_outcome_keeps_exit_code_and_evidence_without_business_failure() -> None:
    outcome = shell_outcome(
        stdout="",
        stderr="grep: no match",
        exit_code=1,
        output_truncated=True,
    )
    assert outcome.business_status is BusinessStatus.NOT_APPLICABLE
    assert outcome.failure_layer is FailureLayer.NONE
    assert outcome.exit_code == 1
    assert "grep: no match" in outcome.evidence.preview
    assert outcome.evidence.output_truncated is True
    assert outcome.is_failure is False


def test_typed_error_outcome_keeps_declared_retryable_and_redacts_evidence() -> None:
    error = ToolError(
        "PLATFORM_AUTH_REJECTED",
        "token=super-secret-value",
        retryable=True,
        platform_sessions={
            "platforms": [
                {"key": "smoke_platform", "available": True},
                {
                    "key": "mssw_bad",
                    "available": False,
                    "error": {
                        "code": "PLATFORM_AUTH_REJECTED",
                        "detail": {"http_status": 401, "message": "signature invalid"},
                    },
                },
            ]
        },
    )
    outcome = error.outcome()
    assert outcome.business_status is BusinessStatus.FAILED
    assert outcome.failure_layer is FailureLayer.TOOL
    assert outcome.retryable is True
    assert "super-secret-value" not in outcome.evidence.preview
    assert (
        "platform_sessions: smoke_platform=ok; "
        "mssw_bad=PLATFORM_AUTH_REJECTED(http=401) signature invalid"
    ) in outcome.evidence.preview


def test_skill_result_outcome_accepts_typed_facts_without_json_parsing() -> None:
    outcome = skill_result_outcome(
        status="SUCCEEDED",
        result={"answer": 42},
        exit_code=0,
    )
    assert outcome.business_status is BusinessStatus.SUCCEEDED
    assert outcome.is_failure is False
