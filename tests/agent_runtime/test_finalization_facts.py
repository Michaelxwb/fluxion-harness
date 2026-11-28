"""`FinalizationFacts` 判定与序列化（design §4.4 / §8 单元）。"""

from __future__ import annotations

import uuid

import pytest
from muad_agent_core.agent.termination import (
    BudgetKind,
    FailureEvidence,
    FailureState,
    PendingToolCall,
    TerminationSnapshot,
)
from muad_agent_runtime.application.finalization import (
    EvidenceStatus,
    ToolCallRecord,
    build_finalization_facts,
)
from muad_agent_runtime.application.run_service import RunService
from sqlalchemy.exc import SQLAlchemyError


def _record(
    *,
    tool: str = "execute",
    tool_call_id: str | None = None,
    business_status: str | None = "SUCCEEDED",
    failure_layer: str | None = "NONE",
    error_code: str | None = None,
    exit_code: int | None = 0,
    retryable: bool | None = None,
    retry_disposition: str | None = None,
    agent_disposition: str | None = None,
    diagnostic: dict | None = None,
    output_truncated: bool | None = False,
    evidence_preview: str = "",
) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name=tool,
        tool_call_id=tool_call_id or f"call-{tool}",
        status="OK",
        business_status=business_status,
        failure_layer=failure_layer,
        error_code=error_code,
        exit_code=exit_code,
        retryable=retryable,
        retry_disposition=retry_disposition,
        agent_disposition=agent_disposition,
        diagnostic=diagnostic,
        output_truncated=output_truncated,
        evidence_preview=evidence_preview,
    )


def _snapshot(reason_status: str, **kwargs) -> TerminationSnapshot:
    return TerminationSnapshot(status=reason_status, **kwargs)


def test_confirmed_business_failure_from_audit() -> None:
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                last_failure=FailureEvidence(
                    tool="execute",
                    tool_call_id="call-execute",
                    code="PLATFORM_AUTH_REJECTED",
                    failure_layer="CONTENT",
                    status="FAILED",
                ),
                no_progress=True,
                no_progress_trigger_call_id="call-execute",
                no_progress_source_call_id="call-execute",
            ),
        ),
        tool_calls=[
            _record(tool="load_skill", business_status="NOT_APPLICABLE", failure_layer=None),
            _record(
                tool="execute",
                business_status="FAILED",
                failure_layer="CONTENT",
                error_code="PLATFORM_AUTH_REJECTED",
                exit_code=3,
                evidence_preview="mssw session rejected",
            ),
        ],
    )

    assert facts.evidence_status == EvidenceStatus.CONFIRMED_FAILURE
    assert facts.evidence_incomplete is False
    assert facts.tool_outcomes.total == 2
    assert facts.tool_outcomes.succeeded == 0
    assert facts.tool_outcomes.failed == 1
    assert facts.tool_outcomes.not_applicable == 1
    assert facts.tool_outcomes.failure_layers == {"TOOL": 0, "CONTENT": 1}
    failure = facts.business_failure_evidence.last_failure
    assert failure is not None
    assert failure.tool == "execute"
    assert failure.error_code == "PLATFORM_AUTH_REJECTED"
    assert failure.exit_code == 3
    assert facts.business_failure_evidence.decisive_failure == failure


def test_confirmed_none_when_business_calls_succeeded() -> None:
    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED",
        snapshot=_snapshot("BUDGET_EXCEEDED", budget_kind=BudgetKind.TURN),
        tool_calls=[
            _record(tool="execute", business_status="SUCCEEDED"),
            _record(tool="workspace_shell", business_status="NOT_APPLICABLE", failure_layer=None),
        ],
    )

    assert facts.evidence_status == EvidenceStatus.CONFIRMED_NONE
    assert facts.evidence_incomplete is False
    assert facts.business_failure_evidence.failures == ()
    assert facts.termination.reason.value == "TURN_BUDGET"


def test_not_observed_without_business_calls() -> None:
    facts = build_finalization_facts(error_code="RUN_BUDGET_EXCEEDED", snapshot=None, tool_calls=[])
    assert facts.evidence_status == EvidenceStatus.NOT_OBSERVED

    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED",
        snapshot=None,
        tool_calls=[_record(tool="workspace_shell", business_status="NOT_APPLICABLE", failure_layer=None)],
    )
    assert facts.evidence_status == EvidenceStatus.NOT_OBSERVED


def test_truncated_output_cannot_be_confirmed_none() -> None:
    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED",
        snapshot=None,
        tool_calls=[
            _record(
                tool="execute",
                business_status="SUCCEEDED",
                output_truncated=True,
            )
        ],
    )

    assert facts.evidence_status == EvidenceStatus.UNKNOWN
    assert facts.evidence_incomplete is True


def test_pre_migration_rows_are_unknown_not_success() -> None:
    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED",
        snapshot=None,
        tool_calls=[
            _record(
                tool="execute",
                business_status=None,
                failure_layer=None,
                exit_code=None,
                output_truncated=None,
            )
        ],
    )

    assert facts.evidence_status == EvidenceStatus.UNKNOWN
    assert facts.tool_outcomes.unknown == 1
    assert facts.tool_outcomes.succeeded == 0


def test_budget_secondary_reason_comes_from_counters() -> None:
    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED",
        snapshot=_snapshot(
            "BUDGET_EXCEEDED",
            budget_kind=BudgetKind.TURN,
            turns_used=20,
            turns_limit=20,
            tool_calls_used=30,
            tool_calls_limit=30,
        ),
    )

    assert facts.termination.reason.value == "TURN_BUDGET"
    assert [reason.value for reason in facts.termination.secondary_reasons] == ["TOOL_BUDGET"]


def test_pending_calls_are_not_failures() -> None:
    facts = build_finalization_facts(
        error_code="RUN_BUDGET_EXCEEDED",
        snapshot=_snapshot(
            "BUDGET_EXCEEDED",
            budget_kind=BudgetKind.TOOL,
            pending_tool_calls=(
                PendingToolCall(call_id="c1", tool_name="execute"),
                PendingToolCall(call_id="c2", tool_name="workspace_shell"),
            ),
        ),
        tool_calls=[],
    )

    assert facts.tool_outcomes.failed == 0
    assert [call["call_id"] for call in facts.pending_tool_calls] == ["c1", "c2"]


def test_payload_has_the_documented_shape() -> None:
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            budget_kind=BudgetKind.NONE,
            turns_used=3,
            turns_limit=20,
            tool_calls_used=2,
            tool_calls_limit=30,
            elapsed_ms=1500,
            deadline_ms=60000,
            failure_state=FailureState(
                no_progress=True,
                no_progress_trigger_call_id="call-execute",
                no_progress_source_call_id="call-execute",
            ),
        ),
        tool_calls=[
            _record(
                tool="execute",
                business_status="FAILED",
                failure_layer="CONTENT",
                error_code="UPSTREAM_5XX",
                exit_code=1,
                retryable=False,
                retry_disposition="BLOCK",
                agent_disposition="STOP",
                diagnostic={"gate_reason": "UNVERIFIED_WRITE", "source_tool_call_id": "call-1"},
                evidence_preview="502",
            )
        ],
    )

    payload = facts.to_payload()
    assert payload["schema_version"] == 1
    assert payload["termination"]["reason"] == "NO_PROGRESS"
    assert payload["termination"]["error_code"] == "RUN_NO_PROGRESS"
    assert payload["usage"]["turns_used"] == 3
    assert payload["tool_outcomes"]["failed"] == 1
    evidence = payload["business_failure_evidence"]
    assert evidence["last_failure"]["error_code"] == "UPSTREAM_5XX"
    assert evidence["trigger_tool_call_id"] == "call-execute"
    assert evidence["source_tool_call_id"] == "call-execute"
    assert evidence["decisive_failure"]["tool"] == "execute"
    assert evidence["decisive_failure"]["retryable"] is False
    assert evidence["decisive_failure"]["retry_disposition"] == "BLOCK"
    assert evidence["decisive_failure"]["agent_disposition"] == "STOP"
    assert evidence["decisive_failure"]["diagnostic"]["gate_reason"] == "UNVERIFIED_WRITE"
    assert payload["evidence_status"] == "CONFIRMED_FAILURE"
    assert payload["completion"] == {"status": "UNKNOWN", "source": None}


def test_snapshot_last_failure_does_not_override_audit_retryable_by_code() -> None:
    """Same tool/code in the snapshot must not turn an explicit audit false into true."""
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                last_failure=FailureEvidence(
                    tool="draft_schedule",
                    tool_call_id="call-draft",
                    code="SCHEDULE_DRAFT_INVALID",
                    failure_layer="TOOL",
                    status="FAILED",
                    retryable=True,
                ),
                no_progress=True,
                no_progress_trigger_call_id="call-draft",
                no_progress_source_call_id="call-draft",
            ),
        ),
        tool_calls=[
            _record(
                tool="draft_schedule",
                tool_call_id="call-draft",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="SCHEDULE_DRAFT_INVALID",
                retryable=False,
            )
        ],
    )

    last = facts.business_failure_evidence.last_failure
    assert last is not None and last.retryable is False


def test_decisive_failure_follows_the_frozen_source_not_the_last_row() -> None:
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                no_progress=True,
                no_progress_trigger_call_id="call-guard",
                no_progress_source_call_id="call-business",
            ),
        ),
        tool_calls=[
            _record(
                tool="draft_schedule",
                tool_call_id="call-business",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
                retryable=False,
                diagnostic={"detail": {"reason": "CONTRACT_MISSING"}},
            ),
            _record(
                tool="draft_schedule",
                tool_call_id="call-guard",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="TOOL_RETRY_UNSAFE",
                retryable=False,
                diagnostic={"gate_reason": "BLOCKED_ATTEMPT"},
            ),
        ],
    )

    evidence = facts.business_failure_evidence
    assert evidence.last_failure is not None
    assert evidence.last_failure.tool_call_id == "call-guard"
    assert evidence.trigger_tool_call_id == "call-guard"
    assert evidence.source_tool_call_id == "call-business"
    assert evidence.decisive_failure is not None
    assert evidence.decisive_failure.tool_call_id == "call-business"
    assert evidence.decisive_failure.error_code == "SCHEDULE_INVOCATION_CONTRACT_MISSING"


def test_tool_failure_uses_the_same_frozen_source_rule() -> None:
    facts = build_finalization_facts(
        error_code="RUN_TOOL_FAILURE_UNRESOLVED",
        snapshot=_snapshot(
            "TOOL_FAILURE",
            failure_state=FailureState(
                no_progress=False,
                failure_pending=True,
                no_progress_trigger_call_id="call-guard",
                no_progress_source_call_id="call-business",
            ),
        ),
        tool_calls=[
            _record(
                tool="draft_schedule",
                tool_call_id="call-business",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
                retryable=False,
            ),
            _record(
                tool="draft_schedule",
                tool_call_id="call-guard",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="TOOL_RETRY_UNSAFE",
                retryable=False,
            ),
        ],
    )

    assert facts.termination.reason.value == "TOOL_FAILURE"
    assert facts.business_failure_evidence.decisive_failure is not None
    assert facts.business_failure_evidence.decisive_failure.tool_call_id == "call-business"


def test_missing_source_row_leaves_the_root_cause_undetermined() -> None:
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                no_progress=True,
                no_progress_trigger_call_id="call-guard",
                no_progress_source_call_id="call-missing",
            ),
        ),
        tool_calls=[
            _record(
                tool="draft_schedule",
                tool_call_id="call-guard",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="TOOL_RETRY_UNSAFE",
                retryable=False,
            )
        ],
    )

    evidence = facts.business_failure_evidence
    assert evidence.last_failure is not None
    assert evidence.last_failure.tool_call_id == "call-guard"
    assert evidence.source_tool_call_id == "call-missing"
    assert evidence.decisive_failure is None, "last row must not impersonate the root cause"


def test_legacy_rows_keep_control_facts_null() -> None:
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                no_progress=True,
                no_progress_trigger_call_id="call-execute",
                no_progress_source_call_id="call-execute",
            ),
        ),
        tool_calls=[
            _record(
                tool="execute",
                tool_call_id="call-execute",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="UPSTREAM_5XX",
            )
        ],
    )

    failure = facts.business_failure_evidence.decisive_failure
    assert failure is not None
    assert failure.retryable is None
    assert failure.retry_disposition is None
    assert failure.agent_disposition is None
    assert failure.diagnostic is None


class _Nested:
    async def __aenter__(self) -> _Nested:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _BrokenSession:
    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        raise SQLAlchemyError("audit unavailable")


@pytest.mark.unit
async def test_audit_read_failure_marks_evidence_incomplete_and_uses_frozen_source() -> None:
    snapshot = _snapshot(
        "NO_PROGRESS",
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="draft_schedule",
                tool_call_id="call-business",
                code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
                failure_layer="TOOL",
                status="FAILED",
            ),
            no_progress=True,
            no_progress_trigger_call_id="call-guard",
            no_progress_source_call_id="call-business",
        ),
    )

    facts = await RunService._finalization_facts(
        object.__new__(RunService),
        _BrokenSession(),
        tenant_id="tenant-a",
        run_id=uuid.uuid4(),
        error_code="RUN_NO_PROGRESS",
        snapshot=snapshot,
    )

    assert facts.evidence_status == EvidenceStatus.UNKNOWN
    assert facts.evidence_incomplete is True
    assert facts.business_failure_evidence.decisive_failure is not None
    assert facts.business_failure_evidence.decisive_failure.tool_call_id == "call-business"


def test_unverified_write_flag_comes_from_the_frozen_state() -> None:
    broken = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                no_progress=True,
                unknown_write_targets=frozenset({"platform:a"}),
            ),
        ),
    )
    clean = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot("NO_PROGRESS", failure_state=FailureState(no_progress=True)),
    )

    assert broken.has_unverified_write is True
    assert clean.has_unverified_write is False
    assert build_finalization_facts(
        error_code="RUN_NO_PROGRESS", snapshot=None
    ).has_unverified_write is None


def test_audit_unavailable_can_fall_back_to_the_frozen_source() -> None:
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                last_failure=FailureEvidence(
                    tool="draft_schedule",
                    tool_call_id="call-business",
                    code="SCHEDULE_INVOCATION_CONTRACT_MISSING",
                    failure_layer="TOOL",
                    status="FAILED",
                ),
                no_progress=True,
                no_progress_trigger_call_id="call-guard",
                no_progress_source_call_id="call-business",
            ),
        ),
        tool_calls=[],
    )

    evidence = facts.business_failure_evidence
    assert evidence.last_failure is not None
    assert evidence.last_failure.tool_call_id == "call-business"
    assert evidence.decisive_failure is not None
    assert evidence.decisive_failure.tool_call_id == "call-business"
