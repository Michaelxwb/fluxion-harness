"""Executor projection: completion outcomes → bounded audit control facts."""

from __future__ import annotations

from muad_agent_core.tools.outcome import (
    AgentDisposition,
    BusinessStatus,
    ExecutionEvidence,
    ExecutionOutcome,
    FailureControl,
    FailureLayer,
    RetryDisposition,
)
from muad_agent_runtime.application.executor import _audit_diagnostic, _control_facts


def _outcome(
    *,
    retryable: bool,
    outcome_known: bool = True,
    diagnostic: dict[str, object] | None = None,
) -> ExecutionOutcome:
    return ExecutionOutcome(
        business_status=BusinessStatus.FAILED,
        failure_layer=FailureLayer.TOOL,
        error_code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
        retryable=retryable,
        control=FailureControl(
            retry_disposition=RetryDisposition.BLOCK,
            agent_disposition=AgentDisposition.STOP,
            outcome_known=outcome_known,
        ),
        evidence=ExecutionEvidence(diagnostic=diagnostic),
    )


def test_control_facts_keep_explicit_false_and_dispositions() -> None:
    outcome = _outcome(
        retryable=False,
        diagnostic={
            "detail": {"reason": "CONTRACT_MISSING", "repair": "REIMPORT_SKILL"},
            "action_owner": "ADMIN",
            "message_source": "PLATFORM",
        },
    )

    retryable, retry_disposition, agent_disposition, diagnostic = _control_facts(outcome)

    assert retryable is False
    assert retry_disposition == "BLOCK"
    assert agent_disposition == "STOP"
    assert diagnostic == {
        "detail": {"reason": "CONTRACT_MISSING", "repair": "REIMPORT_SKILL"},
        "action_owner": "ADMIN",
        "message_source": "PLATFORM",
    }


def test_unknown_outcome_never_stores_a_retry_permission() -> None:
    outcome = _outcome(retryable=True, outcome_known=False)

    assert _control_facts(outcome)[0] is None


def test_diagnostic_projection_drops_unknown_keys_and_invalid_values() -> None:
    diagnostic = _audit_diagnostic(
        _outcome(
            retryable=False,
            diagnostic={
                "detail": {"reason": "CONTRACT_MISSING"},
                "action_owner": "ADMIN",
                "message_source": "PLATFORM",
                "gate_reason": "BLOCKED_ATTEMPT",
                "trigger_tool_call_id": "call-2",
                "source_tool_call_id": "call-1",
                "source_attempt": "skill:demo:sha256:x",
                "write_targets": ["platform:a"],
                "write_operation_key": "order:42",
                "nested": {"secret": "x" * 500},
            },
        )
    )

    assert diagnostic == {
        "detail": {"reason": "CONTRACT_MISSING"},
        "action_owner": "ADMIN",
        "message_source": "PLATFORM",
        "gate_reason": "BLOCKED_ATTEMPT",
        "trigger_tool_call_id": "call-2",
        "source_tool_call_id": "call-1",
        "source_attempt": "skill:demo:sha256:x",
    }


def test_diagnostic_projection_rejects_invalid_enums() -> None:
    assert (
        _audit_diagnostic(
            _outcome(
                retryable=False,
                diagnostic={"action_owner": "EVERYONE", "message_source": "GUESS"},
            )
        )
        is None
    )
