"""Shared failure-state transition: live execution and recovery use the same order."""

from __future__ import annotations

from muad_agent_core.agent.failure import FailureSignal, update_failure_state
from muad_agent_core.agent.termination import FailureState


def _failure(*, retryable: bool = True, code: str = "PLATFORM_TIMEOUT") -> FailureSignal:
    return FailureSignal(
        tool="skill::deliver::run",
        tool_call_id="call-1",
        business_status="FAILED",
        failure_layer="TOOL",
        error_code=code,
        message="try later",
        retryable=retryable,
    )


def _success() -> FailureSignal:
    return FailureSignal(
        tool="skill::deliver::run",
        tool_call_id="call-2",
        business_status="SUCCEEDED",
        failure_layer="NONE",
    )


def test_success_clears_the_retryable_counter_and_a_later_failure_is_progress() -> None:
    state = update_failure_state(FailureState(), _failure(), max_identical_failures=2)
    assert state.no_progress is False

    state = update_failure_state(state, _success(), max_identical_failures=2)
    assert state.identical_failures == {}
    assert state.last_failure is not None, "evidence stays observable after success"

    state = update_failure_state(state, _failure(), max_identical_failures=2)
    assert state.no_progress is False, "success resets the identical-failure chain"
    assert sum(state.identical_failures.values()) == 1


def test_threshold_still_stops_after_repeated_failures_without_success() -> None:
    state = update_failure_state(FailureState(), _failure(), max_identical_failures=2)
    state = update_failure_state(state, _failure(), max_identical_failures=2)

    assert state.no_progress is True
    assert sum(state.identical_failures.values()) == 2


def test_non_retryable_failure_stops_on_the_first_occurrence() -> None:
    state = update_failure_state(
        FailureState(), _failure(retryable=False), max_identical_failures=5
    )

    assert state.no_progress is True
    assert state.last_failure is not None
    assert state.last_failure.retryable is False


def test_cancelled_outcome_does_not_erase_progress_or_failure_facts() -> None:
    state = update_failure_state(FailureState(), _failure(), max_identical_failures=2)
    cancelled = FailureSignal(tool="skill", business_status="CANCELLED")

    updated = update_failure_state(state, cancelled, max_identical_failures=2)

    assert updated == state


def test_no_business_outcome_counts_as_progress() -> None:
    state = update_failure_state(FailureState(), _failure(), max_identical_failures=2)
    updated = update_failure_state(state, None, max_identical_failures=2)

    assert updated.identical_failures == {}
    assert updated.no_progress is False
    assert updated.last_failure is not None
