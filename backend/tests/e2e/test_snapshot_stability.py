from __future__ import annotations

import pytest
from tests.runtime_helpers import (
    bind_skill_to_user,
    publish_resource,
    seed_runtime_profile,
    seed_skill,
)

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


@pytest.mark.asyncio
async def test_S_R03_execution_snapshot_is_fixed_during_hot_publish(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(
        sqlite_store,
        capabilities=[
            {"capability_ref": "search", "version_pin": "latest-published", "type": "skill"}
        ],
    )
    await seed_skill(sqlite_store, version="1")
    await bind_skill_to_user(sqlite_store)

    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=InMemorySessionMemoryStore(),
    )

    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )
    assert context.snapshot.skill_versions == {"search": "1"}

    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        resource_id="search",
        version="2",
        spec={
            "name": "search",
            "description": "new implementation",
            "capability_id": "cap.search",
            "parameters": {},
        },
    )

    result = await runtime.run_step(context, "继续执行")
    assert result.snapshot.skill_versions == {"search": "1"}
    assert context.snapshot.skill_versions == {"search": "1"}

    next_context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )
    assert next_context.snapshot.skill_versions == {"search": "2"}
    assert next_context.snapshot.execution_id != context.snapshot.execution_id
