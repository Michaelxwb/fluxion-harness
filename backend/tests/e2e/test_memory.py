from __future__ import annotations

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.registry import RegistryStore
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore, MemoryPolicy
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


@pytest.mark.asyncio
async def test_S_R17_multi_layer_memory_flush_and_isolation(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    memory_store = InMemorySessionMemoryStore()
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=memory_store,
        memory_policy=MemoryPolicy(max_context_tokens=10, flush_threshold_ratio=0.5),
    )
    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )

    await runtime.memory.add_message(context, "user", "alpha beta gamma delta epsilon")
    assert await memory_store.read_l1("tenant-a", "session-a")

    await runtime.finish_execution(context)
    assert runtime.memory.l0_messages(context.snapshot.execution_id) == []

    same_session = await memory_store.read_l1("tenant-a", "session-a")
    other_session = await memory_store.read_l1("tenant-a", "session-b")
    cross_session = await memory_store.read_l2("tenant-a", "user-a")
    other_tenant = await memory_store.read_l2("tenant-b", "user-a")

    assert any(message.content == "alpha beta gamma delta epsilon" for message in same_session)
    assert other_session == []
    assert any(message.content == "alpha beta gamma delta epsilon" for message in cross_session)
    assert other_tenant == []
