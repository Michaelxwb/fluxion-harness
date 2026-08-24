from __future__ import annotations

from collections.abc import Callable
from statistics import quantiles
from time import perf_counter_ns
from typing import Protocol

from fluxion.resources import ResourceBinding, ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.runtime import RequestContext
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


class BenchmarkFixture(Protocol):
    def pedantic(
        self,
        target: Callable[[], object],
        *,
        iterations: int,
        rounds: int,
    ) -> object: ...


def test_B_R07_snapshot_builder_p95_under_20ms(benchmark: BenchmarkFixture) -> None:
    profile = ResourceDefinition(
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        id="assistant",
        version="1",
        status=ResourceStatus.PUBLISHED,
        spec_json={
            "prompt": "保持严谨",
            "model_policy": {"provider": "test"},
            "allowed_skills": ["search@1"],
        },
    )
    skill = ResourceDefinition(
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        id="search",
        version="1",
        status=ResourceStatus.PUBLISHED,
        spec_json={"name": "search", "capability_id": "cap.search"},
    )
    binding = ResourceBinding(
        binding_id="binding-1",
        tenant_id="tenant-a",
        subject_type="user",
        subject_id="user-a",
        resource_type=ResourceKind.SKILL,
        resource_id="search",
        resource_version_selector="1",
    )
    builder = ExecutionSnapshotBuilder(ResourceResolver.from_cache())
    request = RequestContext(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
    )
    durations_ms: list[float] = []

    def build_snapshot() -> object:
        started = perf_counter_ns()
        snapshot = builder.build_from_resolved(
            request,
            runtime_profile=profile,
            skills=[skill],
            bindings=[binding],
        )
        durations_ms.append((perf_counter_ns() - started) / 1_000_000)
        return snapshot

    benchmark.pedantic(build_snapshot, iterations=1, rounds=200)
    p95_ms = quantiles(durations_ms, n=20, method="inclusive")[18]
    assert p95_ms <= 20.0
