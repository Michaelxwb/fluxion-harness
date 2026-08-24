from __future__ import annotations

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.registry import RegistryStore
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


@pytest.mark.asyncio
async def test_S_R05_pod_replacement_keeps_facts_in_shared_memory_store(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    shared_memory = InMemorySessionMemoryStore()

    pod1 = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=shared_memory,
    )
    request = RequestContext(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
    )
    await pod1.run(request, input_messages=["用户事实: project=atlas"])
    assert pod1.local_durable_fact_count == 0

    pod2 = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=shared_memory,
    )
    context = await pod2.start_execution(request.with_new_execution())
    messages = await pod2.memory.read_session_context(context)

    assert any(message.content == "用户事实: project=atlas" for message in messages)
    assert pod2.local_durable_fact_count == 0
