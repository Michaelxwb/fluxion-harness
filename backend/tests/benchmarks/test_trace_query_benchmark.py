from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from fluxion.resources import ExecutionSnapshot
from fluxion.runtime import InMemoryTraceStore, TraceRecord


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_C107_recent_execution_trace_query_p95_under_500ms(
    benchmark: BenchmarkFixture,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = InMemoryTraceStore()
    loop.run_until_complete(_seed_traces(store, count=5_000))
    since = datetime.now(UTC) - timedelta(days=7)
    latencies_ms: list[float] = []

    def run_once() -> object:
        started = perf_counter_ns()
        records = loop.run_until_complete(
            store.query_by_execution(
                tenant_id="tenant-a",
                execution_id="execution-target",
                since=since,
                limit=100,
            )
        )
        latencies_ms.append((perf_counter_ns() - started) / 1_000_000)
        assert records
        assert all(record.execution_id == "execution-target" for record in records)
        return records

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=200)
        assert quantiles(latencies_ms, n=20, method="inclusive")[18] <= 500.0
    finally:
        loop.close()


async def _seed_traces(store: InMemoryTraceStore, *, count: int) -> None:
    now = datetime.now(UTC)
    for index in range(count):
        execution_id = "execution-target" if index % 100 == 0 else f"execution-{index}"
        created_at = now - timedelta(minutes=index % 20_000)
        snapshot = ExecutionSnapshot(
            execution_id=execution_id,
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="runtime-main",
            runtime_profile_version="1",
            model_resolution={"provider": "dev.echo"},
            trace_id=f"trace-{index}",
            created_at=created_at,
        )
        await store.append(
            TraceRecord(
                trace_id=snapshot.trace_id,
                execution_id=execution_id,
                tenant_id="tenant-a",
                runtime_profile_id="runtime-main",
                runtime_profile_version="1",
                snapshot=snapshot,
                events=(),
                latency_ms=1.0,
                error=None,
            )
        )
