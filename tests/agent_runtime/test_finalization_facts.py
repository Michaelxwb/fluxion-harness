"""`FinalizationFacts` 判定与序列化（design §4.4 / §8 单元）。"""

from __future__ import annotations

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


def _record(
    *,
    tool: str = "execute",
    business_status: str | None = "SUCCEEDED",
    failure_layer: str | None = "NONE",
    error_code: str | None = None,
    exit_code: int | None = 0,
    output_truncated: bool | None = False,
    evidence_preview: str = "",
) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name=tool,
        tool_call_id=f"call-{tool}",
        status="OK",
        business_status=business_status,
        failure_layer=failure_layer,
        error_code=error_code,
        exit_code=exit_code,
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
                    code="PLATFORM_AUTH_REJECTED",
                    failure_layer="CONTENT",
                    status="FAILED",
                ),
                no_progress=True,
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
        ),
        tool_calls=[
            _record(
                tool="execute",
                business_status="FAILED",
                failure_layer="CONTENT",
                error_code="UPSTREAM_5XX",
                exit_code=1,
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
    assert payload["business_failure_evidence"]["last_failure"]["error_code"] == "UPSTREAM_5XX"
    assert payload["business_failure_evidence"]["decisive_failure"]["tool"] == "execute"
    assert payload["evidence_status"] == "CONFIRMED_FAILURE"
    assert payload["completion"] == {"status": "UNKNOWN", "source": None}


def test_retryable_failure_flag_comes_from_the_frozen_failure_state() -> None:
    """审计行没有 retryable 列；同一失败以 Runner 冻结事实为准，避免误渲染为不可重试。"""
    facts = build_finalization_facts(
        error_code="RUN_NO_PROGRESS",
        snapshot=_snapshot(
            "NO_PROGRESS",
            failure_state=FailureState(
                last_failure=FailureEvidence(
                    tool="draft_schedule",
                    code="SCHEDULE_DRAFT_INVALID",
                    failure_layer="TOOL",
                    status="FAILED",
                    retryable=True,
                ),
                no_progress=True,
            ),
        ),
        tool_calls=[
            _record(
                tool="draft_schedule",
                business_status="FAILED",
                failure_layer="TOOL",
                error_code="SCHEDULE_DRAFT_INVALID",
                evidence_preview="TOOL_NOT_OWNED",
            )
        ],
    )

    last = facts.business_failure_evidence.last_failure
    assert last is not None and last.retryable is True
