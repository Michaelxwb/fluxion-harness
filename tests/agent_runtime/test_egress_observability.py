from datetime import UTC, datetime

import pytest
from muad_agent_runtime.application.egress_observability import (
    EgressAttemptEvent,
    aggregate_interface_events,
    deduplicate_attempt_events,
)
from muad_agent_runtime.application.egress_spool import EgressAuditSpool


def event(attempt, phase, result="COMPLETED", *, second=1):
    return EgressAttemptEvent(
        "tenant",
        attempt,
        phase,
        "platform",
        "GET /assets",
        datetime(2026, 1, 1, 12, 0, second, tzinfo=UTC),
        result,
        True,
        result != "BLOCKED",
        10,
    )


def test_attempt_phase_replay_is_deduplicated():
    assert (
        len(deduplicate_attempt_events([event("a", "send"), event("a", "send"), event("a", "complete")])) == 2
    )


def test_aggregation_keeps_allowed_and_actual_send_distinct():
    rows = aggregate_interface_events([event("a", "send"), event("b", "blocked", "BLOCKED")])
    assert rows[0].attempted == 2
    assert rows[0].allowed == 2
    assert rows[0].sent == 1
    assert rows[0].blocked == 1
    assert rows[0].latency_total_ms == 20


def test_invalid_bucket_is_rejected():
    with pytest.raises(ValueError):
        aggregate_interface_events([], bucket_seconds=0)


def test_spool_is_bounded_and_replayable(tmp_path):
    spool = EgressAuditSpool(tmp_path / "egress.jsonl", max_bytes=128)
    assert spool.append({"attempt_id": "a", "phase": "send"})
    assert spool.drain() == [{"attempt_id": "a", "phase": "send"}]
    assert spool.drain() == []


def test_spool_overflow_is_explicit(tmp_path):
    spool = EgressAuditSpool(tmp_path / "egress.jsonl", max_bytes=4)
    assert spool.append({"attempt_id": "too-large"}) is False
