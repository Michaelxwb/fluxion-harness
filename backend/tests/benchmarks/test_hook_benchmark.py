from __future__ import annotations

import asyncio
from collections.abc import Callable
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    FailPolicy,
    HookRegistration,
    HookScope,
    TypedEventBus,
)


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


async def _noop(_payload: BeforeToolCallPayload) -> None:
    return None


def test_B_R05_hook_dispatch_p95_under_10ms(benchmark: BenchmarkFixture) -> None:
    bus = TypedEventBus()
    for index in range(3):
        bus.register(
            HookRegistration(
                registration_id=f"hook-{index}",
                event_type=BeforeToolCallPayload,
                priority=index,
                timeout_ms=100,
                fail_policy=FailPolicy.FAIL_CLOSED,
                scope=HookScope.GLOBAL,
                handler=_noop,
            )
        )
    payload = BeforeToolCallPayload(
        tenant_id="tenant-a",
        execution_id="execution-a",
        trace_id="trace-a",
        tool_id="search",
        arguments={},
    )
    durations_ms: list[float] = []

    def dispatch() -> object:
        started = perf_counter_ns()
        result = asyncio.run(bus.dispatch(payload))
        durations_ms.append((perf_counter_ns() - started) / 1_000_000)
        return result

    benchmark.pedantic(dispatch, iterations=1, rounds=200)
    p95_ms = quantiles(durations_ms, n=20, method="inclusive")[18]
    assert p95_ms <= 10.0
