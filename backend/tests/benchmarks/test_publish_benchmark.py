from __future__ import annotations

import asyncio
from collections.abc import Callable
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from httpx import ASGITransport, AsyncClient
from tests.console_helpers import create_resource, publish_resource

from fluxion.api.console import create_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.console_app import ConsoleApplicationService


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_C105_publish_api_p95_under_500ms(benchmark: BenchmarkFixture) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = ConsoleApplicationService(store)
    client = AsyncClient(transport=ASGITransport(app=create_app(service)), base_url="http://console")
    loop.run_until_complete(service.initialize())
    latencies_ms: list[float] = []
    run_index = 0

    def run_once() -> object:
        nonlocal run_index
        run_index += 1
        resource_id = f"assistant-{run_index}"
        loop.run_until_complete(
            create_resource(client, kind=ResourceKind.RUNTIME_PROFILE, resource_id=resource_id)
        )
        started = perf_counter_ns()
        response = loop.run_until_complete(
            publish_resource(client, kind=ResourceKind.RUNTIME_PROFILE, resource_id=resource_id)
        )
        latencies_ms.append((perf_counter_ns() - started) / 1_000_000)
        assert response.json()["data"]["event_status"] == "pending"
        return response

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=100)
        assert quantiles(latencies_ms, n=20, method="inclusive")[18] <= 500.0
    finally:
        loop.run_until_complete(client.aclose())
        loop.run_until_complete(service.close())
        loop.close()
