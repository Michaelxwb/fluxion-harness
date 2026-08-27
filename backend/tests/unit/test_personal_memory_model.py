"""ADR-MEM-001 TASK-003 B-01：personal_memory 模型 + MemoryLearner.commit。

真实边界（契约声明）：真实 SQLAlchemy async SQLite engine + create_all 建出
真实 `personal_memory` 表（非 mock schema），commit / list / update / delete
全部走真实表查询。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.registry.schema import metadata
from fluxion.runtime.personal_memory import (
    ConsentDecision,
    MemoryCandidate,
    MemoryLearner,
    MemoryType,
    PersonalMemoryStore,
    PolicyDecision,
)


@pytest.fixture
async def personal_memory_store() -> AsyncGenerator[PersonalMemoryStore, None]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    try:
        yield PersonalMemoryStore(engine)
    finally:
        await engine.dispose()


def _candidate() -> MemoryCandidate:
    return MemoryCandidate(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_type=MemoryType.SEMANTIC,
        content="user prefers concise answers",
        source_session_id="session-a",
        source_range_hash="a" * 64,
    )


_ALLOW_POLICY = PolicyDecision(allowed=True)
_ALLOW_CONSENT = ConsentDecision(allowed=True)


async def test_b01_learning_disabled_rejects_new_personal_memory(
    personal_memory_store: PersonalMemoryStore,
) -> None:
    learner = MemoryLearner(personal_memory_store)

    result = await learner.commit(
        _candidate(),
        policy_decision=_ALLOW_POLICY,
        consent=_ALLOW_CONSENT,
        learning_enabled=False,
    )

    assert result.committed is False
    assert result.reason == "learning_disabled"
    assert result.entry_id is None
    # 真实表查询：learning_enabled=false 不写入任何行（NFR-PRIV-01 user control）
    assert await personal_memory_store.list_entries("tenant-a", "user-a") == []


async def test_b01_existing_memory_viewable_correctable_deletable(
    personal_memory_store: PersonalMemoryStore,
) -> None:
    learner = MemoryLearner(personal_memory_store)
    committed = await learner.commit(
        _candidate(),
        policy_decision=_ALLOW_POLICY,
        consent=_ALLOW_CONSENT,
        learning_enabled=True,
    )
    assert committed.committed is True
    assert committed.entry_id is not None

    # 查看（NFR-PRIV-01）：已写入条目可见，provenance 完整
    entries = await personal_memory_store.list_entries("tenant-a", "user-a")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == committed.entry_id
    assert entry.memory_type is MemoryType.SEMANTIC
    assert entry.content == "user prefers concise answers"
    assert entry.source_session_id == "session-a"
    assert entry.source_range_hash == "a" * 64
    assert entry.created_at is not None
    assert entry.updated_at is not None

    # tenant 隔离（NFR-SEC-01）：同 user_id 跨 tenant 不可见
    assert await personal_memory_store.list_entries("tenant-b", "user-a") == []

    # 纠正：update_content 只改 content 并刷新 updated_at
    corrected = await personal_memory_store.update_content(
        "tenant-a", "user-a", entry.id, "user prefers concise answers (corrected)"
    )
    assert corrected is not None
    assert corrected.content == "user prefers concise answers (corrected)"
    assert corrected.updated_at >= entry.updated_at
    # 跨 tenant 纠正不得命中
    assert (
        await personal_memory_store.update_content(
            "tenant-b", "user-a", entry.id, "must not apply"
        )
        is None
    )

    # 删除：delete 后不可见；重复删除返回 False
    assert await personal_memory_store.delete("tenant-a", "user-a", entry.id) is True
    assert await personal_memory_store.list_entries("tenant-a", "user-a") == []
    assert await personal_memory_store.delete("tenant-a", "user-a", entry.id) is False
