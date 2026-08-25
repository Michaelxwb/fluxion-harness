from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from httpx import ASGITransport, AsyncClient
from tests.console_helpers import tenant_headers

from fluxion.api.console import create_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.services.console_app import ConsoleApplicationService


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_C104_resource_list_and_detail_p95_under_300ms(
    benchmark: BenchmarkFixture,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = ConsoleApplicationService(store)
    client = AsyncClient(
        transport=ASGITransport(app=create_app(service)),
        base_url="http://console",
    )
    loop.run_until_complete(service.initialize())
    loop.run_until_complete(_seed_resources(store, count=500))
    latencies_ms: list[float] = []

    def run_once() -> object:
        started = perf_counter_ns()
        listed = loop.run_until_complete(
            client.get(
                "/api/v1/resources?resource_type=runtime_profile&page=1&page_size=50",
                headers=tenant_headers(),
            )
        )
        detailed = loop.run_until_complete(
            client.get(
                "/api/v1/resources/runtime_profile/runtime-250",
                headers=tenant_headers(),
            )
        )
        latencies_ms.append((perf_counter_ns() - started) / 1_000_000)
        assert listed.status_code == detailed.status_code == 200
        assert listed.json()["data"]["total"] == 500
        assert detailed.json()["data"]["resource_id"] == "runtime-250"
        return detailed

    try:
        benchmark.pedantic(run_once, iterations=1, rounds=100)
        assert quantiles(latencies_ms, n=20, method="inclusive")[18] <= 300.0
    finally:
        loop.run_until_complete(client.aclose())
        loop.run_until_complete(service.close())
        loop.close()


async def _seed_resources(store: SQLiteRegistryStore, *, count: int) -> None:
    for index in range(count):
        resource_id = f"runtime-{index}"
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id=resource_id,
                tenant_id="tenant-a",
                version="1",
                status=ResourceStatus.PUBLISHED,
                spec_json={"id": resource_id, "model": "dev"},
                published_at=datetime.now(UTC),
            )
        )
