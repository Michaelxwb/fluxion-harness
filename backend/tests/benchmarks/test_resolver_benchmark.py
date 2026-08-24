from __future__ import annotations

from collections.abc import Callable
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus, TenantResourceCache
from fluxion.runtime.resolver import ResourceResolver


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_R04_resolver_l1_hit_p95_under_5ms(benchmark: BenchmarkFixture) -> None:
    cache = TenantResourceCache(ttl_seconds=60)
    resource = ResourceDefinition(
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        id="search",
        version="1",
        status=ResourceStatus.PUBLISHED,
        spec_json={"name": "search", "capability_id": "cap.search"},
    )
    cache.set(resource)
    resolver = ResourceResolver.from_cache(cache)
    durations_ms: list[float] = []

    def resolve_cached() -> ResourceDefinition:
        started = perf_counter_ns()
        resolved = resolver.resolve_from_l1(
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="search",
            selector="1",
        )
        durations_ms.append((perf_counter_ns() - started) / 1_000_000)
        return resolved

    benchmark.pedantic(resolve_cached, iterations=1, rounds=200)
    p95_ms = quantiles(durations_ms, n=20, method="inclusive")[18]
    assert p95_ms <= 5.0
