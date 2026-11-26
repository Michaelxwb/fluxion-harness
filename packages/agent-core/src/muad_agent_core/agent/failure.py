"""Failure, progress and retry decisions shared by live execution and recovery."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..tools.outcome import (
    FAILURE_STATUSES,
    AgentDisposition,
    BusinessStatus,
    FailureLayer,
    FailureScope,
    SecurityDisposition,
)
from .termination import FailureEvidence, FailureState


@dataclass(frozen=True, slots=True)
class FailureSignal:
    tool: str
    tool_call_id: str = ""
    business_status: str = BusinessStatus.NOT_APPLICABLE.value
    failure_layer: str = FailureLayer.NONE.value
    error_code: str | None = None
    message: str = ""
    exit_code: int | None = None
    retryable: bool = False
    agent_disposition: str = "CONTINUE"
    retry_disposition: str = "ALLOW"
    scope: str = "CALL"
    sent: bool = False
    outcome_known: bool = True
    evidence_status: str = "CONFIRMED"
    retry_after: str | None = None
    security_disposition: str = "NORMAL"
    boundary_kind: str = "NONE"
    stop_confirmed: bool = True
    evidence_preview: str = ""
    occurred_at: str = ""
    attempt: str | None = None
    external_started: bool = False
    review_progress: tuple[str, int, int] | None = None


def is_effectful(effect: str, retry_safety: str) -> bool:
    # A distinct model call has a new operation identity, even for a declared idempotent tool.
    return effect != "READ" and retry_safety != "READ_ONLY"


def failure_scope(tool: str, *, skill_key: str | None = None, skill_id: object = None) -> str:
    if skill_key:
        return "skill:" + skill_key
    return "skill:" + str(skill_id) if skill_id else tool


def attempt_identity(scope: str, args_hash: str) -> str:
    return f"{scope}:{args_hash}"


def is_hard_tool_failure(failure: FailureEvidence) -> bool:
    return (
        failure.code in {"LOCAL_BUDGET_EXHAUSTED", "EGRESS_STOP_UNCONFIRMED", "EGRESS_METERING_UNAVAILABLE"}
        or failure.agent_disposition == AgentDisposition.STOP
        or failure.scope == FailureScope.RUN
        or failure.security_disposition == SecurityDisposition.VIOLATION
        or not failure.stop_confirmed
    )


def update_failure_state(
    state: FailureState,
    signal: FailureSignal | None,
    *,
    max_identical_failures: int,
) -> FailureState:
    if signal is None:
        return state
    if state.no_progress and state.last_failure is not None and is_hard_tool_failure(state.last_failure):
        return state
    if signal.business_status in FAILURE_STATUSES:
        evidence = _evidence(signal)
        if is_hard_tool_failure(evidence):
            return replace(state, last_failure=evidence, no_progress=True)
    if signal.review_progress is not None:
        return _review_progress(state, signal, max_identical_failures=max_identical_failures)
    status = signal.business_status
    if status not in FAILURE_STATUSES or signal.failure_layer == FailureLayer.NONE:
        if status == BusinessStatus.SUCCEEDED:
            counts = {
                key: count for key, count in state.identical_failures.items()
                if not key.startswith(signal.tool + ":")
            }
            return replace(state, identical_failures=counts)
        return state

    blocked = state.blocked_attempts
    if not signal.retryable and signal.attempt is not None:
        blocked = blocked | {signal.attempt}
    if signal.external_started:
        state = replace(state, external_blocked=True)
    state = replace(state, blocked_attempts=blocked)
    key_code = signal.error_code or "TOOL_FAILED"
    if key_code == "TOOL_RETRY_UNSAFE":
        key_code = "TOOL_RETRY_BLOCKED"
    return _count_failure(
        state, _evidence(signal), key=f"{signal.tool}:{key_code}:{signal.failure_layer}",
        max_identical_failures=max_identical_failures,
    )


def _evidence(signal: FailureSignal) -> FailureEvidence:
    return FailureEvidence(
        tool=signal.tool,
        tool_call_id=signal.tool_call_id,
        status=signal.business_status,
        code=signal.error_code or "TOOL_FAILED",
        message=signal.message,
        failure_layer=signal.failure_layer,
        exit_code=signal.exit_code,
        retryable=signal.retryable,
        agent_disposition=signal.agent_disposition,
        retry_disposition=signal.retry_disposition,
        scope=signal.scope,
        sent=signal.sent,
        outcome_known=signal.outcome_known,
        evidence_status=signal.evidence_status,
        retry_after=signal.retry_after,
        security_disposition=signal.security_disposition,
        boundary_kind=signal.boundary_kind,
        stop_confirmed=signal.stop_confirmed,
        evidence_preview=signal.evidence_preview,
        occurred_at=signal.occurred_at,
    )


def _count_failure(
    state: FailureState, evidence: FailureEvidence, *, key: str, max_identical_failures: int,
) -> FailureState:
    counts = dict(state.identical_failures)
    counts[key] = counts.get(key, 0) + 1
    return replace(
        state, last_failure=evidence, identical_failures=counts,
        no_progress=state.no_progress or counts[key] >= max(2, max_identical_failures),
    )


def _review_progress(
    state: FailureState, signal: FailureSignal, *, max_identical_failures: int,
) -> FailureState:
    assert signal.review_progress is not None
    draft, revision, errors = signal.review_progress
    previous = state.reviews.get(draft)
    key = f"review:{draft}:NO_PROGRESS"
    if previous is None or errors == 0 or (revision > previous[0] and errors < previous[1]):
        counts = dict(state.identical_failures)
        counts.pop(key, None)
        return replace(state, reviews={**state.reviews, draft: (revision, errors)}, identical_failures=counts)
    evidence = replace(_evidence(signal), code="SKILL_REVIEW_NO_PROGRESS")
    return _count_failure(state, evidence, key=key, max_identical_failures=max_identical_failures)


__all__ = ["FailureSignal", "attempt_identity", "failure_scope", "is_effectful", "update_failure_state"]
