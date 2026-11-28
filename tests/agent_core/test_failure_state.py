"""Shared failure-state transition: live execution and recovery use the same order."""

from __future__ import annotations

from muad_agent_core.agent.failure import (
    RETRY_GATE_UNVERIFIED_WRITE,
    FailureSignal,
    has_unverified_write,
    overlaps_unknown_write,
    record_unverified_write,
    unverified_write_sources,
    update_failure_state,
)
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


def test_non_retryable_failure_preserves_a_chance_to_respond() -> None:
    state = update_failure_state(
        FailureState(), _failure(retryable=False), max_identical_failures=5
    )

    assert state.no_progress is False
    assert state.last_failure is not None
    assert state.last_failure.retryable is False


def test_cancelled_outcome_does_not_erase_progress_or_failure_facts() -> None:
    state = update_failure_state(FailureState(), _failure(), max_identical_failures=2)
    cancelled = FailureSignal(tool="skill", business_status="CANCELLED")

    updated = update_failure_state(state, cancelled, max_identical_failures=2)

    assert updated == state


def test_no_business_outcome_does_not_reset_failures() -> None:
    state = update_failure_state(FailureState(), _failure(), max_identical_failures=2)
    updated = update_failure_state(state, None, max_identical_failures=2)

    assert updated.identical_failures == state.identical_failures
    assert updated.no_progress is False
    assert updated.last_failure is not None


def test_unknown_business_outcome_retains_unverified_write_target() -> None:
    signal = FailureSignal(
        tool="execute", business_status="UNKNOWN", outcome_known=False,
        external_started=True, write_targets=frozenset({"platform:a"}),
    )

    state = update_failure_state(FailureState(), signal, max_identical_failures=2)

    restored = FailureState.from_payload(state.as_payload())
    assert overlaps_unknown_write(restored.unknown_write_targets, frozenset({"platform:a"}))
    assert not overlaps_unknown_write(restored.unknown_write_targets, frozenset())
    assert not overlaps_unknown_write(restored.unknown_write_targets, frozenset({"platform:b"}))
    assert not restored.no_progress


def test_normal_load_skill_result_clears_the_tool_failure_counter() -> None:
    refused = FailureSignal(tool="load_skill", business_status="DENIED", failure_layer="TOOL")
    state = update_failure_state(FailureState(), refused, max_identical_failures=2)

    state = update_failure_state(state, FailureSignal(tool="load_skill"), max_identical_failures=2)

    assert not state.identical_failures


def test_trusted_operation_identity_takes_precedence_over_the_target() -> None:
    targets = frozenset({"platform:a"})
    signal = FailureSignal(
        tool="write", business_status="UNKNOWN", outcome_known=False,
        external_started=True, write_targets=targets, write_operation_key="order:42",
    )

    state = update_failure_state(FailureState(), signal, max_identical_failures=2)
    restored = FailureState.from_payload(state.as_payload())

    assert has_unverified_write(
        restored, targets, "order:42", fallback_target="tool:write"
    )
    assert has_unverified_write(restored, targets, None, fallback_target="tool:write")
    assert not has_unverified_write(
        restored, targets, "order:43", fallback_target="tool:write"
    )
    assert not has_unverified_write(
        restored,
        targets,
        "order:42",
        fallback_target="tool:write",
        retry_safety="IDEMPOTENT",
    )


def test_trusted_success_clears_the_same_operation_key() -> None:
    targets = frozenset({"platform:a"})
    state = update_failure_state(
        FailureState(),
        FailureSignal(
            tool="write",
            business_status="UNKNOWN",
            outcome_known=False,
            external_started=True,
            write_targets=targets,
            write_operation_key="order:42",
        ),
        max_identical_failures=2,
    )

    state = update_failure_state(
        state,
        FailureSignal(
            tool="write",
            business_status="SUCCEEDED",
            write_targets=targets,
            write_operation_key="order:42",
        ),
        max_identical_failures=2,
    )

    assert not state.unknown_write_operations
    assert state.unknown_write_targets == frozenset()


def test_unverified_write_source_is_the_first_failure_and_clears_with_the_operation() -> None:
    targets = frozenset({"platform:a"})
    state = record_unverified_write(
        FailureState(),
        targets,
        "order:42",
        fallback_target="tool:write",
        source_call_id="call-1",
    )
    state = record_unverified_write(
        state,
        targets,
        "order:42",
        fallback_target="tool:write",
        source_call_id="call-2",
    )

    assert unverified_write_sources(
        state, targets, "order:42", fallback_target="tool:write"
    ) == frozenset({"call-1"})
    restored = FailureState.from_payload(state.as_payload())
    assert restored.unknown_operation_sources == {"order:42": "call-1"}

    state = update_failure_state(
        restored,
        FailureSignal(
            tool="write",
            business_status="SUCCEEDED",
            write_targets=targets,
            write_operation_key="order:42",
        ),
        max_identical_failures=2,
    )

    assert unverified_write_sources(
        state, targets, "order:42", fallback_target="tool:write"
    ) is None
    assert state.unknown_operation_sources == {}


def test_overlapping_unknown_targets_keep_each_candidate_source() -> None:
    state = record_unverified_write(
        FailureState(),
        frozenset({"platform:a"}),
        None,
        fallback_target="tool:write",
        source_call_id="call-a",
    )
    state = record_unverified_write(
        state,
        frozenset({"platform:b"}),
        None,
        fallback_target="tool:write",
        source_call_id="call-b",
    )

    assert unverified_write_sources(
        state, frozenset({"platform:a"}), None, fallback_target="tool:write"
    ) == frozenset({"call-a"})
    assert unverified_write_sources(
        state, frozenset({"platform:a", "platform:b"}), None, fallback_target="tool:write"
    ) == frozenset({"call-a", "call-b"})


def test_blocked_attempt_keeps_the_first_source_and_survives_checkpoint_roundtrip() -> None:
    state = update_failure_state(
        FailureState(),
        FailureSignal(
            tool="skill::deliver::run",
            tool_call_id="call-1",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="PLATFORM_AUTH_REJECTED",
            retryable=False,
            attempt="attempt-1",
        ),
        max_identical_failures=5,
    )
    state = update_failure_state(
        state,
        FailureSignal(
            tool="skill::deliver::run",
            tool_call_id="call-2",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="PLATFORM_AUTH_REJECTED",
            retryable=False,
            attempt="attempt-1",
        ),
        max_identical_failures=5,
    )

    assert state.blocked_attempts == frozenset({"attempt-1"})
    assert state.blocked_attempt_sources == {"attempt-1": "call-1"}

    restored = FailureState.from_payload(state.as_payload())
    assert restored.blocked_attempt_sources == state.blocked_attempt_sources

    orphan = FailureState.from_payload(
        {
            "blocked_attempts": ["attempt-1"],
            "blocked_attempt_sources": {
                "attempt-1": "call-1",
                "attempt-removed": "call-9",
            },
        }
    )
    assert orphan.blocked_attempt_sources == {"attempt-1": "call-1"}


def test_no_progress_freezes_the_direct_trigger_as_its_own_source() -> None:
    state = update_failure_state(
        FailureState(),
        _failure(retryable=False),
        max_identical_failures=2,
    )
    state = update_failure_state(state, _failure(retryable=False), max_identical_failures=2)
    assert state.no_progress is True
    assert state.no_progress_trigger_call_id == "call-1"
    assert state.no_progress_source_call_id == "call-1"

    state = update_failure_state(
        state,
        FailureSignal(
            tool="skill::deliver::run",
            tool_call_id="call-2",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="PLATFORM_AUTH_REJECTED",
            retryable=False,
            attempt="skill::deliver::run:",
        ),
        max_identical_failures=2,
    )
    assert state.no_progress_trigger_call_id == "call-1"
    assert state.no_progress_source_call_id == "call-1"


def test_gate_trigger_uses_only_the_explicit_business_source() -> None:
    key = "skill::deliver::run:TOOL_RETRY_BLOCKED:TOOL"
    blocked = FailureState(
        blocked_attempts=frozenset({"skill::deliver::run:abc"}),
        blocked_attempt_sources={"skill::deliver::run:abc": "call-1"},
        identical_failures={key: 1},
    )
    state = update_failure_state(
        blocked,
        FailureSignal(
            tool="skill::deliver::run",
            tool_call_id="call-2",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="TOOL_RETRY_UNSAFE",
            retryable=False,
            attempt="skill::deliver::run:abc",
            source_call_id="call-1",
        ),
        max_identical_failures=2,
    )

    assert state.no_progress is True
    assert state.no_progress_trigger_call_id == "call-2"
    assert state.no_progress_source_call_id == "call-1"

    ambiguous = update_failure_state(
        blocked,
        FailureSignal(
            tool="skill::deliver::run",
            tool_call_id="call-3",
            business_status="FAILED",
            failure_layer="TOOL",
            error_code="TOOL_RETRY_UNSAFE",
            retryable=False,
            attempt="skill::deliver::run:abc",
        ),
        max_identical_failures=2,
    )
    assert ambiguous.no_progress_trigger_call_id == "call-3"
    assert ambiguous.no_progress_source_call_id == ""


def test_unverified_write_gate_reason_is_stable() -> None:
    assert RETRY_GATE_UNVERIFIED_WRITE == "UNVERIFIED_WRITE"
