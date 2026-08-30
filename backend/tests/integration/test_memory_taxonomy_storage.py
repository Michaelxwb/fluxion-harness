"""ADR-MEM-001 TASK-001 验收测试：存储层缺陷修复（双写 + cross-read + summary 重命名）。

S-01 / S-02（integration，RULE-fluxion-runtime-001 verifier: session-memory-externalized）：

- 真实边界：`SQLSessionMemoryStore`（sqlite+aiosqlite，非 mock）的 flush 写入路径
  + read_l2/read_l1 真实 SQL level 过滤。
- S-01 断言：flush 一批 records 只写 L1（`session_memory.level=l1`），不写 L2
  ——双写缺陷：`_flush_new_records` 既 `append_l1` 又 `append_l2`
  （`memory.py:190-191`）；`read_l1` 返回 session raw。
- S-02 断言：`read_l2` 不含 SessionContextSummary（`level=l2` only，删 cross-read
  `level.in_(L2, summary)`，`memory_sql.py:54`）；`read_l1` 含 SessionContextSummary。

RED 约定（cf-task:start #7）：双写 + cross-read 是 design §2.1 已核实真实缺陷
（非 green-before）。先写测试记录 RED——S-01 双写下 `session_memory` 出现
`level=l2` 行；S-02 cross-read 下 `read_l2` 返回 summary 记录。修复后 GREEN。
不得伪造失败。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from fluxion.registry import RegistryStore
from fluxion.registry.schema import session_memory
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import MemoryPolicy, MemoryRecord
from fluxion.runtime.memory_sql import SQLSessionMemoryStore
from fluxion.runtime.resolver import ResourceResolver
from fluxion.services.context_resolver import ContextResolver, ContextResolverSnapshotBuilder
from tests.runtime_helpers import seed_runtime_profile


async def _level_counts(store: RegistryStore, tenant_id: str) -> dict[str, int]:
    """直接查 `session_memory` 表的 level 分布——证明双写缺陷的最底层证据。

    绕过 store 的 read 方法（那些是修复对象），用裸 SQL 聚合 level 列，
    使 S-01 的"只写 L1 不写 L2"断言落在数据层而非读侧方法。
    """
    engine = getattr(store, "engine")
    statement = (
        select(session_memory.c.level, func.count())
        .where(session_memory.c.tenant_id == tenant_id)
        .group_by(session_memory.c.level)
    )
    async with engine.connect() as connection:
        rows = (await connection.execute(statement)).all()
    return {str(row[0]): int(row[1]) for row in rows}


# ---- S-01: flush 只写 L1，不写 L2 ----


@pytest.mark.asyncio
async def test_s01_flush_writes_only_l1_not_l2(sqlite_store: RegistryStore) -> None:
    await seed_runtime_profile(sqlite_store)
    engine = getattr(sqlite_store, "engine")
    memory_store = SQLSessionMemoryStore(engine)
    runtime = AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(sqlite_store)),
        memory_store=memory_store,
        # 5 词 = 5 tokens ≥ threshold(10*0.5=5) → 单条消息即触发 flush
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

    # 双写缺陷修复（memory.py:190-191）：flush 只写 L1，session_memory 不应出现 level=l2 行
    counts = await _level_counts(sqlite_store, "tenant-a")
    assert counts.get("l1", 0) >= 1, "flush 应写入 L1（session raw）"
    assert counts.get("l2", 0) == 0, "ADR-MEM-001: flush 停双写，不应写入 L2（legacy user-raw）"

    # read_l1 返回 session raw（durable 路径，session-memory-externalized）
    l1_records = await memory_store.read_l1("tenant-a", "session-a")
    assert any(r.content == "alpha beta gamma delta epsilon" for r in l1_records)


# ---- S-02: read_l2 不含 SessionContextSummary；read_l1 含 ----


@pytest.mark.asyncio
async def test_s02_read_l2_excludes_session_context_summary(
    sqlite_store: RegistryStore,
) -> None:
    engine = getattr(sqlite_store, "engine")
    store = SQLSessionMemoryStore(engine)

    # SessionContextSummary（session-scoped compaction 输出，由 append_summary 写入）
    summary = MemoryRecord(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        execution_id="exec-1",
        role="summary",
        content="session compaction summary",
        tokens=5,
    )
    await store.append_summary(summary)

    # legacy L2 user-raw（显式 append_l2 写入，模拟停双写后仍残留的 legacy 数据）
    legacy_l2 = MemoryRecord(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        execution_id="exec-1",
        role="user",
        content="legacy user raw",
        tokens=5,
    )
    await store.append_l2([legacy_l2])

    # cross-read 缺陷修复（memory_sql.py:54）：read_l2 只读 level=l2，不含 SessionContextSummary
    l2_records = await store.read_l2("tenant-a", "user-a")
    assert [r.content for r in l2_records] == ["legacy user raw"]
    assert not any(r.role == "summary" for r in l2_records), (
        "read_l2 不得 cross-read SessionContextSummary（session 摘要泄漏进 user-level retrieval）"
    )

    # read_l1 含 SessionContextSummary（session-scoped，保留 level IN (l1, session_context_summary)）
    l1_records = await store.read_l1("tenant-a", "session-a")
    assert any(r.role == "summary" for r in l1_records), (
        "read_l1 应含 SessionContextSummary（session compaction 上下文）"
    )
