from __future__ import annotations

import asyncio

import pytest
from tests.runtime_helpers import bind_skill_to_user, seed_runtime_profile, seed_skill

from fluxion.registry import RegistryStore
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ResourceResolver
from fluxion.services.context_resolver import ContextResolver, ContextResolverSnapshotBuilder


@pytest.mark.asyncio
async def test_B_R03_runtime_pool_resolves_same_versions(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(
        sqlite_store,
        capabilities=[{"capability_ref": "search", "version_pin": "1", "type": "skill"}],
    )
    await seed_skill(sqlite_store, version="1")
    await bind_skill_to_user(sqlite_store)

    pod_a = AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(sqlite_store)),
        memory_store=InMemorySessionMemoryStore(),
    )
    pod_b = AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(sqlite_store)),
        memory_store=InMemorySessionMemoryStore(),
    )
    request = RequestContext(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
    )

    first, second = await asyncio.gather(
        pod_a.start_execution(request),
        pod_b.start_execution(request.with_new_execution()),
    )

    assert first.snapshot.runtime_profile_version == second.snapshot.runtime_profile_version
    assert first.snapshot.skill_versions == second.snapshot.skill_versions == {"search": "1"}
    assert first.snapshot.execution_id != second.snapshot.execution_id
