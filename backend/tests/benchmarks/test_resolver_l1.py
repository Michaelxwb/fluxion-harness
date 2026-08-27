"""TASK-010 / NFR-PERF-01：ResourceResolver L1 命中路径 P95 ≤ 5ms。

"性能敏感路径按最优设计"的验收锚点：L1 进程内缓存命中（无 IO）是
Resource Resolver 快路径契约；超时即证明缓存退化或键解析引入了意外开销。
"""

from __future__ import annotations

from statistics import quantiles
from time import perf_counter_ns
from typing import Callable, Protocol

import pytest

from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime.resolver import ResourceResolver


class BenchmarkFixture(Protocol):
    def pedantic(self, target: Callable[[], object], *, iterations: int, rounds: int) -> object: ...


def _definition(kind_value: str) -> ResourceDefinition:
    return ResourceDefinition(
        tenant_id="tenant-a",
        kind=ResourceKind(kind_value),
        id="assistant",
        version="1",
        status=ResourceStatus.PUBLISHED,
        spec_json={},
    )


@pytest.mark.parametrize(
    "kind_value",
    ["runtime_profile", "agent_definition", "skill"],
)
def test_nfr_perf_01_resolver_l1_hit_p95_under_5ms(
    benchmark: BenchmarkFixture, kind_value: str
) -> None:
    resolver = ResourceResolver.from_cache()
    resource = _definition(kind_value)
    # 双键预热：精确版本 + latest-published 别名（与 resolver 正常读取路径一致）。
    resolver._cache.set(resource)  # noqa: SLF001 - bench 直接构造 L1 热态
    resolver._cache.set(resource, version_alias="latest-published")  # noqa: SLF001

    durations_ms: list[float] = []

    def hit_once() -> object:
        started = perf_counter_ns()
        found = resolver.resolve_from_l1(
            tenant_id="tenant-a",
            kind=resource.kind,
            resource_id="assistant",
            selector="latest-published",
        )
        durations_ms.append((perf_counter_ns() - started) / 1_000_000)
        return found

    found = hit_once()
    assert found.id == "assistant"

    benchmark.pedantic(hit_once, iterations=1, rounds=5000)
    p95_ms = quantiles(durations_ms, n=20, method="inclusive")[18]
    # L1 命中为纯内存 dict 读：给 jit/解释器波动留足余量，实际应远低于 1ms。
    assert p95_ms <= 5.0
