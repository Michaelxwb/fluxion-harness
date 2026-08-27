from __future__ import annotations

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.registry import RegistryStore
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore, MemoryPolicy
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


@pytest.mark.asyncio
async def test_S_R18_context_compaction_preserves_latest_raw_and_snapshot(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    memory_store = InMemorySessionMemoryStore()
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=memory_store,
        memory_policy=MemoryPolicy(max_context_tokens=12, retain_latest_turns=2),
    )
    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )
    snapshot_before = context.snapshot.model_dump(mode="json")

    for index in range(5):
        await runtime.memory.add_message(context, "user", f"turn-{index}")

    compacted = await runtime.memory.compact_context(context)
    summaries = await memory_store.read_summaries("tenant-a", "session-a")

    assert [message.content for message in compacted.raw_messages] == ["turn-3", "turn-4"]
    assert "turn-0" in compacted.summary
    assert "turn-2" in compacted.summary
    assert summaries and summaries[-1].content == compacted.summary
    # ADR-MEM-001：append_summary 不交叉写 L2——SessionContextSummary 只服务
    # session compaction，不泄漏进 user-level retrieval。read_l2 为空。
    assert await memory_store.read_l2("tenant-a", "user-a") == []
    assert context.snapshot.model_dump(mode="json") == snapshot_before
    # 已摘要的 L1 记录被截断，仅保留 summary 与 retain 窗口
    l1_after = await memory_store.read_l1("tenant-a", "session-a")
    assert [message.content for message in l1_after] == [compacted.summary]


@pytest.mark.asyncio
async def test_S_R18_repeated_compaction_does_not_summarize_summaries(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    memory_store = InMemorySessionMemoryStore()
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=memory_store,
        memory_policy=MemoryPolicy(max_context_tokens=12, retain_latest_turns=2),
    )
    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )

    for index in range(5):
        await runtime.memory.add_message(context, "user", f"turn-{index}")
    first = await runtime.memory.compact_context(context)
    for index in range(5, 7):
        await runtime.memory.add_message(context, "user", f"turn-{index}")
    second = await runtime.memory.compact_context(context)

    # ADR-MEM-001：假 `_summarize` 已删——默认 registry 走确定性截断 fallback 输出
    assert first.summary == "turn-0 | turn-1 | turn-2"
    # 第二次压缩只概括新消息，不把既有 summary 再摘要一遍
    assert second.summary == "turn-3 | turn-4"
    assert [message.content for message in second.raw_messages] == ["turn-5", "turn-6"]
