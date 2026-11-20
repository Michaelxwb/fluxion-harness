"""Worker Task 路径复用 TASK-008 的 ExecutionOutcome 契约。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from muad_agent_core.tools import BusinessStatus, FailureLayer
from muad_agent_worker.worker.execution_outcomes import (
    FailureDisposition,
    OutcomeKind,
    classify_failure,
    interpret_execution,
)
from muad_contracts import ToolRetrySafety


def _now() -> datetime:
    return datetime.now(UTC)


def _execution(status: str, *, stderr: str = "", stdout: str = "", result=None, exit_code=0):
    return {
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "exit_code": exit_code,
    }


def test_task_error_message_keeps_platform_summary() -> None:
    from muad_agent_worker.worker.executor import TaskExecutionError
    from muad_agent_worker.worker.service import _describe_error

    error = TaskExecutionError(
        "SKILL_EXECUTION_FAILED",
        "skill failed",
        platform_sessions={
            "platforms": [
                {"key": "mssp", "available": True},
                {
                    "key": "mssw",
                    "available": False,
                    "error": {
                        "code": "PLATFORM_AUTH_REJECTED",
                        "detail": {"http_status": 401, "message": "denied"},
                    },
                },
            ]
        },
    )

    code, message, retryable = _describe_error(error)

    assert code == "SKILL_EXECUTION_FAILED"
    assert "platform_sessions: mssp=ok; mssw=PLATFORM_AUTH_REJECTED(http=401) denied" in message


def test_failed_skill_uses_the_failure_envelope() -> None:
    envelope = json.dumps(
        {
            "ok": False,
            "code": "PLATFORM_AUTH_REJECTED",
            "message": "mssw session rejected: HTTP 401",
            "retryable": False,
        }
    )
    outcome = interpret_execution(
        _execution("FAILED", stderr=envelope, exit_code=3), now=_now()
    )

    assert outcome.kind is OutcomeKind.RETRYABLE_FAILURE
    assert outcome.error_code == "PLATFORM_AUTH_REJECTED"
    assert outcome.business_status == str(BusinessStatus.FAILED)
    assert outcome.failure_layer == str(FailureLayer.CONTENT)
    assert outcome.exit_code == 3
    assert outcome.retryable is False
    assert "mssw session rejected" in outcome.evidence_preview


def test_failed_skill_without_envelope_is_deterministic() -> None:
    outcome = interpret_execution(_execution("FAILED", stderr="boom"), now=_now())

    assert outcome.kind is OutcomeKind.RETRYABLE_FAILURE
    assert outcome.error_code == "SKILL_EXECUTION_FAILED"
    assert outcome.retryable is False


def test_failed_skill_with_retryable_envelope_keeps_retryable() -> None:
    envelope = json.dumps({"ok": False, "code": "UPSTREAM_5XX", "retryable": True})
    outcome = interpret_execution(_execution("FAILED", stderr=envelope), now=_now())

    assert outcome.retryable is True
    assert outcome.error_code == "UPSTREAM_5XX"


def test_timeout_is_retryable_content_failure() -> None:
    outcome = interpret_execution(_execution("TIMED_OUT", stderr="too slow"), now=_now())

    assert outcome.business_status == str(BusinessStatus.TIMED_OUT)
    assert outcome.failure_layer == str(FailureLayer.CONTENT)
    assert outcome.retryable is True


def test_cancelled_is_not_a_failure() -> None:
    outcome = interpret_execution(_execution("CANCELLED", stderr="stopped"), now=_now())

    assert outcome.kind is OutcomeKind.CANCELLED
    assert outcome.business_status == str(BusinessStatus.CANCELLED)
    assert outcome.failure_layer == str(FailureLayer.NONE)


def test_deterministic_business_failure_does_not_retry() -> None:
    envelope = json.dumps(
        {"ok": False, "code": "PLATFORM_AUTH_REJECTED", "message": "401", "retryable": False}
    )
    outcome = interpret_execution(_execution("FAILED", stderr=envelope), now=_now())

    decision = classify_failure(
        retry_safety=ToolRetrySafety.IDEMPOTENT,
        error_code=outcome.error_code or "SKILL_EXECUTION_FAILED",
        error_message=outcome.error_message,
        retryable=bool(outcome.retryable),
    )

    assert decision.disposition is FailureDisposition.FAILED
    assert decision.error_code == "PLATFORM_AUTH_REJECTED"


def test_retryable_business_failure_follows_retry_safety() -> None:
    envelope = json.dumps({"ok": False, "code": "UPSTREAM_5XX", "retryable": True})
    outcome = interpret_execution(_execution("FAILED", stderr=envelope), now=_now())

    retry = classify_failure(
        retry_safety=ToolRetrySafety.READ_ONLY,
        error_code=outcome.error_code or "SKILL_EXECUTION_FAILED",
        error_message=outcome.error_message,
        retryable=bool(outcome.retryable),
    )
    unverified = classify_failure(
        retry_safety=ToolRetrySafety.NON_RETRYABLE,
        error_code=outcome.error_code or "SKILL_EXECUTION_FAILED",
        error_message=outcome.error_message,
        retryable=bool(outcome.retryable),
    )

    assert retry.disposition is FailureDisposition.RETRY
    assert unverified.disposition is FailureDisposition.UNVERIFIED
