"""Failure, progress and retry decisions shared by live execution and recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from muad_contracts.platform_settings import MIN_IDENTICAL_FAILURES

from ..tools.outcome import (
    FAILURE_STATUSES,
    AgentDisposition,
    BusinessStatus,
    FailureLayer,
    FailureScope,
    SecurityDisposition,
)
from .termination import FailureEvidence, FailureState

#: Why the pre-dispatch gate refused a call. Frozen into the tool error and failure state.
RETRY_GATE_BLOCKED_ATTEMPT = "BLOCKED_ATTEMPT"
RETRY_GATE_UNVERIFIED_WRITE = "UNVERIFIED_WRITE"
RETRY_GATE_GUARDED = "RETRY_GUARDED"


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
    action_owner: str | None = None
    hint_known: bool | None = None
    occurred_at: str = ""
    attempt: str | None = None
    #: The call that originally produced the failure this gate/signal points at.
    source_call_id: str = ""
    external_started: bool = False
    write_targets: frozenset[str] = frozenset()
    write_operation_key: str | None = None
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


def overlaps_unknown_write(unknown: frozenset[str], targets: frozenset[str]) -> bool:
    """New writes always carry a bounded target; no legacy wildcard is accepted."""
    return bool(unknown & targets)


def unverified_write_sources(
    state: FailureState,
    targets: frozenset[str],
    operation_key: str | None,
    *,
    fallback_target: str,
    retry_safety: str = "NON_RETRYABLE",
) -> frozenset[str] | None:
    """Return the candidate source call IDs when the write cannot be verified.

    ``None`` means no unverified write overlaps. An empty set means a write is
    unknown but no unique source can be proven (legacy state or conflicting
    sources); callers must not guess one.
    """
    bounded_targets = targets or frozenset({fallback_target})
    sources: set[str] = set()
    matched = False
    for target in state.unknown_write_targets & bounded_targets:
        matched = True
        source = state.unknown_target_sources.get(target)
        sources.add(source or "")
    if operation_key is not None:
        if operation_key in state.unknown_write_operations:
            # An idempotent operation may safely repeat with the same host-owned key.
            if retry_safety != "IDEMPOTENT":
                matched = True
                source = state.unknown_operation_sources.get(operation_key)
                sources.add(source or "")
    else:
        for key, unknown_targets in state.unknown_write_operations.items():
            if overlaps_unknown_write(unknown_targets, bounded_targets):
                matched = True
                source = state.unknown_operation_sources.get(key)
                sources.add(source or "")
    if "" in sources:
        return frozenset()
    return frozenset(sources) if matched else None


def has_unverified_write(
    state: FailureState,
    targets: frozenset[str],
    operation_key: str | None,
    *,
    fallback_target: str,
    retry_safety: str = "NON_RETRYABLE",
) -> bool:
    return unverified_write_sources(
        state,
        targets,
        operation_key,
        fallback_target=fallback_target,
        retry_safety=retry_safety,
    ) is not None


def record_unverified_write(
    state: FailureState,
    targets: frozenset[str],
    operation_key: str | None,
    *,
    fallback_target: str,
    source_call_id: str = "",
) -> FailureState:
    bounded_targets = targets or frozenset({fallback_target})
    if operation_key is None:
        target_sources = dict(state.unknown_target_sources)
        for target in bounded_targets:
            if target not in target_sources and source_call_id:
                target_sources[target] = source_call_id
        return replace(
            state,
            unknown_write_targets=state.unknown_write_targets | bounded_targets,
            unknown_target_sources=target_sources,
        )
    previous = state.unknown_write_operations.get(operation_key, frozenset())
    operation_sources = dict(state.unknown_operation_sources)
    if operation_key not in operation_sources and source_call_id:
        operation_sources[operation_key] = source_call_id
    return replace(
        state,
        unknown_write_operations={
            **state.unknown_write_operations, operation_key: previous | bounded_targets,
        },
        unknown_operation_sources=operation_sources,
    )


def clear_unverified_write(state: FailureState, operation_key: str | None) -> FailureState:
    if operation_key is None or operation_key not in state.unknown_write_operations:
        return state
    return replace(
        state,
        unknown_write_operations={
            key: value for key, value in state.unknown_write_operations.items() if key != operation_key
        },
        unknown_operation_sources={
            key: value
            for key, value in state.unknown_operation_sources.items()
            if key != operation_key
        },
    )


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
    if signal.external_started and not signal.outcome_known:
        state = record_unverified_write(
            state,
            signal.write_targets,
            signal.write_operation_key,
            fallback_target=f"tool:{signal.tool}",
            source_call_id=signal.tool_call_id,
        )
    evidence = (
        _evidence(signal) if signal.business_status in FAILURE_STATUSES else None
    )
    if evidence is not None and is_hard_tool_failure(evidence):
        return replace(
            state, last_failure=evidence, failure_pending=True, no_progress=True,
            no_progress_trigger_call_id=signal.tool_call_id,
            no_progress_source_call_id=signal.source_call_id or signal.tool_call_id,
        )
    if signal.review_progress is not None:
        return _review_progress(state, signal, max_identical_failures=max_identical_failures)
    status = signal.business_status
    if status not in FAILURE_STATUSES or signal.failure_layer == FailureLayer.NONE:
        if (
            status in {BusinessStatus.SUCCEEDED, BusinessStatus.NOT_APPLICABLE}
            and signal.failure_layer == FailureLayer.NONE and signal.outcome_known
        ):
            counts = {
                key: count for key, count in state.identical_failures.items()
                if not key.startswith(signal.tool + ":")
            }
            state = clear_unverified_write(state, signal.write_operation_key)
            pending = state.failure_pending
            previous = state.last_failure
            if previous is not None:
                same_operation = (
                    signal.write_operation_key is not None
                    and signal.write_operation_key == previous.write_operation_key
                )
                same_target = bool(
                    previous.write_targets
                    and signal.write_targets
                    and previous.write_targets & signal.write_targets
                )
                if previous.tool == signal.tool or same_operation or same_target:
                    # A successful corrective call for the same tool/operation resolves it.
                    pending = False
            return replace(state, identical_failures=counts, failure_pending=pending)
        return state

    blocked = state.blocked_attempts
    attempt_sources = state.blocked_attempt_sources
    if not signal.retryable and signal.attempt is not None:
        if signal.attempt not in blocked:
            blocked = blocked | {signal.attempt}
        if signal.attempt not in attempt_sources and signal.tool_call_id:
            # Repeated failures of the same unresolved attempt keep the first source.
            attempt_sources = {**attempt_sources, signal.attempt: signal.tool_call_id}
    key_code = signal.error_code or "TOOL_FAILED"
    if key_code == "TOOL_RETRY_UNSAFE":
        key_code = "TOOL_RETRY_BLOCKED"
    return _count_failure(
        state,
        evidence if evidence is not None else _evidence(signal),
        key=f"{signal.tool}:{key_code}:{signal.failure_layer}",
        max_identical_failures=max_identical_failures,
        source_call_id=signal.source_call_id,
        blocked_attempts=blocked,
        blocked_attempt_sources=attempt_sources,
        failure_pending=True,
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
        action_owner=signal.action_owner,
        hint_known=signal.hint_known,
        write_targets=signal.write_targets,
        write_operation_key=signal.write_operation_key,
        occurred_at=signal.occurred_at,
    )


def _count_failure(
    state: FailureState,
    evidence: FailureEvidence,
    *,
    key: str,
    max_identical_failures: int,
    source_call_id: str = "",
    blocked_attempts: frozenset[str] | None = None,
    blocked_attempt_sources: Mapping[str, str] | None = None,
    failure_pending: bool | None = None,
) -> FailureState:
    counts = dict(state.identical_failures)
    counts[key] = counts.get(key, 0) + 1
    no_progress = state.no_progress or counts[key] >= max(
        MIN_IDENTICAL_FAILURES, max_identical_failures
    )
    blocked = blocked_attempts if blocked_attempts is not None else state.blocked_attempts
    sources = (
        blocked_attempt_sources
        if blocked_attempt_sources is not None
        else state.blocked_attempt_sources
    )
    pending = failure_pending if failure_pending is not None else state.failure_pending
    if state.no_progress or not no_progress:
        return replace(
            state,
            last_failure=evidence,
            failure_pending=pending,
            identical_failures=counts,
            no_progress=no_progress,
            blocked_attempts=blocked,
            blocked_attempt_sources=sources,
        )
    # Freeze the first trigger/source pair; later failures must not rewrite the root cause.
    trigger = evidence.tool_call_id
    # A gate block points at an explicit business source; a direct failure is its own source.
    # An empty source on a gate block means the relation is ambiguous, so it must stay empty.
    source = source_call_id if evidence.code == "TOOL_RETRY_UNSAFE" else trigger
    return replace(
        state,
        last_failure=evidence,
        failure_pending=pending,
        identical_failures=counts,
        no_progress=True,
        blocked_attempts=blocked,
        blocked_attempt_sources=sources,
        no_progress_trigger_call_id=trigger,
        no_progress_source_call_id=source,
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
        pending = state.failure_pending
        if state.last_failure is not None and state.last_failure.tool == signal.tool:
            pending = False
        return replace(
            state,
            reviews={**state.reviews, draft: (revision, errors)},
            identical_failures=counts,
            failure_pending=pending,
        )
    evidence = replace(_evidence(signal), code="SKILL_REVIEW_NO_PROGRESS")
    return _count_failure(
        state,
        evidence,
        key=key,
        max_identical_failures=max_identical_failures,
        failure_pending=True,
    )


__all__ = [
    "RETRY_GATE_BLOCKED_ATTEMPT",
    "RETRY_GATE_GUARDED",
    "RETRY_GATE_UNVERIFIED_WRITE",
    "FailureSignal",
    "attempt_identity",
    "clear_unverified_write",
    "failure_scope",
    "has_unverified_write",
    "is_effectful",
    "overlaps_unknown_write",
    "record_unverified_write",
    "unverified_write_sources",
    "update_failure_state",
]
