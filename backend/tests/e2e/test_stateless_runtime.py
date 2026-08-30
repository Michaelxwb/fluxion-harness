from __future__ import annotations

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.registry import RegistryStore, SQLiteRegistryStore
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.memory_sql import SQLSessionMemoryStore
from fluxion.runtime.resolver import ResourceResolver
from fluxion.services.context_resolver import ContextResolver, ContextResolverSnapshotBuilder


def _runtime_with_sql_memory(store: RegistryStore) -> AgentRuntime:
    engine = getattr(store, "engine", None)
    assert engine is not None, "SQLSessionMemoryStore requires a SQLAlchemy-backed store"
    return AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(store)),
        memory_store=SQLSessionMemoryStore(engine),
    )


def _request() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
    )


@pytest.mark.asyncio
async def test_S_R05_memory_persists_across_store_instances(tmp_path) -> None:
    """Pod 替换/重启后，用户级记忆从共享 Registry 读取而非进程内 dict。"""
    dsn = f"sqlite+aiosqlite:///{tmp_path}/fluxion-memory.db"

    store1 = SQLiteRegistryStore(dsn)
    await store1.initialize()
    await seed_runtime_profile(store1)
    pod1 = _runtime_with_sql_memory(store1)
    await pod1.run(_request(), input_messages=["用户事实: project=atlas"])
    await store1.close()

    # 全新 store/engine 模拟旧 Pod 已销毁、新 Pod 启动；runtime_profile 已持久化在同一文件。
    store2 = SQLiteRegistryStore(dsn)
    await store2.initialize()
    pod2 = _runtime_with_sql_memory(store2)
    context = await pod2.start_execution(_request().with_new_execution())
    messages = await pod2.memory.read_session_context(context)
    await store2.close()

    assert any(message.content == "用户事实: project=atlas" for message in messages)


@pytest.mark.asyncio
async def test_S_R05_in_memory_store_remains_supported(sqlite_store: RegistryStore) -> None:
    """InMemorySessionMemoryStore 仍是合法的无 store 测试夹具实现。"""
    await seed_runtime_profile(sqlite_store)
    pod = AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(sqlite_store)),
        memory_store=InMemorySessionMemoryStore(),
    )
    await pod.run(_request(), input_messages=["用户事实: project=atlas"])
    context = await pod.start_execution(_request().with_new_execution())
    messages = await pod.memory.read_session_context(context)
    assert any(message.content == "用户事实: project=atlas" for message in messages)
