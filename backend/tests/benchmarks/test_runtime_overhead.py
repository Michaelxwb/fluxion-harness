from __future__ import annotations

import asyncio
from collections.abc import Callable
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from fluxion.registry import SQLiteRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import seed_agent_definition


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_R06_runtime_framework_overhead_p95_under_50ms_p99_under_100ms(
    benchmark: BenchmarkFixture,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    service = loop.run_until_complete(_build_service())
    durations_ms: list[float] = []
    run_index = 0

    def run_once() -> object:
        nonlocal run_index
        run_index += 1
        started = perf_counter_ns()
        result = loop.run_until_complete(
            service.run(
                RunRuntimeRequest(
                    tenant_id="tenant-a",
                    user_id="user-a",
                    runtime_profile_id="assistant",
                    session_id=f"session-{run_index}",
                    input_message="ping",
                )
            )
        )
        durations_ms.append((perf_counter_ns() - started) / 1_000_000)
        return result

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=200)
        p95_ms = quantiles(durations_ms, n=20, method="inclusive")[18]
        p99_ms = quantiles(durations_ms, n=100, method="inclusive")[98]
        assert p95_ms <= 50.0
        assert p99_ms <= 100.0
    finally:
        loop.run_until_complete(service.close())
        loop.close()


async def _build_service() -> RuntimeApplicationService:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = RuntimeApplicationService.create_dev_bundle(store, cache_ttl_seconds=600)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            request_timeout_ms=1_000,
        )
    )
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
        )
    )
    await seed_agent_definition(store, agent_id="assistant", provider_id="dev.echo")
    await service.run(
        RunRuntimeRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="warmup",
            input_message="warmup",
        )
    )
    return service
