"""Failure tracking as one pure state machine shared by live execution and recovery.

The runner applies tool outcomes in round order; recovery folds the same completed facts in the
same order. Keeping the transition here is what makes a restored Run stop for the same reasons a
live Run would.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..tools.outcome import BusinessStatus, FailureLayer
from .termination import FailureEvidence, FailureState


@dataclass(frozen=True, slots=True)
class FailureSignal:
    """One tool outcome expressed as the facts failure tracking consumes."""

    tool: str
    tool_call_id: str = ""
    business_status: str = BusinessStatus.NOT_APPLICABLE.value
    failure_layer: str = FailureLayer.NONE.value
    error_code: str | None = None
    message: str = ""
    exit_code: int | None = None
    retryable: bool = False
    evidence_preview: str = ""
    occurred_at: str = ""


def update_failure_state(
    state: FailureState,
    signal: FailureSignal | None,
    *,
    max_identical_failures: int,
) -> FailureState:
    """Apply one outcome. `None` means "no confirmed failure" (success / non-business call)."""
    if signal is None:
        return _progress(state)
    status = signal.business_status
    if status in {BusinessStatus.CANCELLED, BusinessStatus.RATE_LIMITED}:
        # These outcomes preserve previously observed failures and retry counts.
        return state
    if (
        status
        in (
            BusinessStatus.SUCCEEDED,
            BusinessStatus.NOT_APPLICABLE,
            BusinessStatus.UNKNOWN,
        )
        or signal.failure_layer == FailureLayer.NONE
    ):
        return _progress(state)

    evidence = FailureEvidence(
        tool=signal.tool,
        tool_call_id=signal.tool_call_id,
        status=status,
        code=signal.error_code or "TOOL_FAILED",
        message=signal.message,
        failure_layer=signal.failure_layer,
        exit_code=signal.exit_code,
        retryable=signal.retryable,
        evidence_preview=signal.evidence_preview,
        occurred_at=signal.occurred_at,
    )
    if not signal.retryable:
        return replace(state, last_failure=evidence, no_progress=True)

    counts = dict(state.identical_failures)
    key = f"{evidence.tool}:{evidence.code}:{evidence.failure_layer}"
    counts[key] = counts.get(key, 0) + 1
    stopped = state.no_progress or counts[key] >= max(1, max_identical_failures)
    return replace(
        state,
        last_failure=evidence,
        identical_failures=counts,
        no_progress=stopped,
    )


def _progress(state: FailureState) -> FailureState:
    """Reset retry counters without undoing a stop reached earlier in the same batch."""
    return replace(state, identical_failures={})


__all__ = ["FailureSignal", "update_failure_state"]
