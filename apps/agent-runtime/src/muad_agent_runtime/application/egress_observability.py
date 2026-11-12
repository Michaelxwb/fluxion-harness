"""Deterministic egress attempt deduplication and interface aggregation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class EgressAttemptEvent:
    tenant_id: str
    attempt_id: str
    phase: str
    target_instance_id: str
    route_id: str
    occurred_at: datetime
    result_status: str
    allowed: bool
    sent: bool
    latency_ms: int | None = None
    error_code: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, str]:
        if not self.attempt_id.strip() or not self.phase.strip():
            raise ValueError("attempt_id and phase are required")
        return self.attempt_id, self.phase


@dataclass(frozen=True, slots=True)
class EgressInterfaceSummary:
    tenant_id: str
    target_instance_id: str
    route_id: str
    bucket_start: datetime
    attempted: int
    allowed: int
    sent: int
    completed: int
    blocked: int
    failed: int
    latency_total_ms: int


def deduplicate_attempt_events(events: Iterable[EgressAttemptEvent]) -> tuple[EgressAttemptEvent, ...]:
    """Keep the first durable event for each attempt phase; replay cannot inflate counts."""
    seen: set[tuple[str, str]] = set()
    result: list[EgressAttemptEvent] = []
    for event in events:
        key = event.dedupe_key
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return tuple(result)


def aggregate_interface_events(
    events: Iterable[EgressAttemptEvent], *, bucket_seconds: int = 60
) -> tuple[EgressInterfaceSummary, ...]:
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    groups: dict[tuple[str, str, str, datetime], list[EgressAttemptEvent]] = defaultdict(list)
    for event in deduplicate_attempt_events(events):
        moment = event.occurred_at
        bucket = moment - timedelta(seconds=moment.second % bucket_seconds, microseconds=moment.microsecond)
        groups[(event.tenant_id, event.target_instance_id, event.route_id, bucket)].append(event)
    summaries = []
    for (tenant, target, route, bucket), items in sorted(groups.items()):
        summaries.append(
            EgressInterfaceSummary(
                tenant,
                target,
                route,
                bucket,
                attempted=len(items),
                allowed=sum(item.allowed for item in items),
                sent=sum(item.sent for item in items),
                completed=sum(item.result_status == "COMPLETED" for item in items),
                blocked=sum(item.result_status == "BLOCKED" for item in items),
                failed=sum(item.result_status == "FAILED" for item in items),
                latency_total_ms=sum(item.latency_ms or 0 for item in items),
            )
        )
    return tuple(summaries)
