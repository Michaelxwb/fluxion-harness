"""TASK-005（phase2）Personal Memory 查看/纠正/删除 + reindex 验收测试。

NFR-PRIV-01 后端契约（design §2.3.1 FEAT-P2-08）：
- 用户可 查看/纠正/删除 自己的 Personal Memory；
- 纠正内容后 reindex（embedding 重算回写）；
- 纠正/删除后缓存失效钩子被调用（cache-aside：先写库再失效）；
- reindex 不存在的条目 → 明确错误（不静默）。

真实边界：真实 SQLite personal_memory 表 + PersonalMemoryStore + MemoryLearner
+ PgVectorSemanticStore（embedding 重算）+ 记录式缓存失效钩子；不 mock。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.memory.application.memory_user_service import MemoryUserService
from fluxion.memory.domain.personal_memory import (
    ConsentDecision,
    MemoryCandidate,
    MemoryType,
    PolicyDecision,
)
from fluxion.registry import SQLiteRegistryStore

TENANT = "tenant-a"
USER = "user-a"


def _candidate(content: str) -> MemoryCandidate:
    return MemoryCandidate(
        tenant_id=TENANT,
        user_id=USER,
        memory_type=MemoryType.SEMANTIC,
        content=content,
        source_session_id="s1",
        source_range_hash="h1",
    )


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store.engine
    finally:
        await store.close()


@pytest.fixture
async def service(engine: AsyncEngine) -> MemoryUserService:
    svc = MemoryUserService(engine)
    await svc.commit_candidate(
        candidate=_candidate("旧内容"),
        policy_decision=PolicyDecision(allowed=True, reason="ok"),
        consent=ConsentDecision(allowed=True, reason="ok"),
    )
    return svc


@pytest.mark.asyncio
async def test_s07_view_entries(service: MemoryUserService) -> None:
    """查看：用户可见自己的记忆条目（tenant/user scope）。"""
    entries = await service.list_entries(tenant_id=TENANT, user_id=USER)
    assert len(entries) == 1 and entries[0].content == "旧内容"


@pytest.mark.asyncio
async def test_nfr_priv01_correct_updates_and_reindexes(
    service: MemoryUserService,
) -> None:
    """纠正：内容更新 + embedding 重算回写 + 缓存失效钩子（cache-aside）。"""
    entries = await service.list_entries(tenant_id=TENANT, user_id=USER)
    entry_id = entries[0].id

    invalidated: list[str] = []
    updated = await service.correct(
        tenant_id=TENANT,
        user_id=USER,
        entry_id=entry_id,
        content="新内容（已纠正）",
        on_cache_invalidate=invalidated.append,
    )
    assert updated is not None and updated.content == "新内容（已纠正）"
    # embedding 已重算（DB 行级核验：embedding 非 None）
    row = await service._get_entry(tenant_id=TENANT, user_id=USER, entry_id=entry_id)
    assert row is not None and row["embedding"] is not None
    # 缓存失效钩子：先写库后失效
    assert invalidated == [f"fluxion:mem:{TENANT}:{USER}:semantic"]


@pytest.mark.asyncio
async def test_nfr_priv01_delete_removes_and_invalidates(
    service: MemoryUserService,
) -> None:
    """删除：条目移除 + 缓存失效；再查为空。"""
    entries = await service.list_entries(tenant_id=TENANT, user_id=USER)
    entry_id = entries[0].id
    memory_type = entries[0].memory_type

    invalidated: list[str] = []
    deleted = await service.delete(
        tenant_id=TENANT,
        user_id=USER,
        entry_id=entry_id,
        memory_type=memory_type,
        on_cache_invalidate=invalidated.append,
    )
    assert deleted is True
    assert invalidated == [f"fluxion:mem:{TENANT}:{USER}:semantic"]
    assert await service.list_entries(tenant_id=TENANT, user_id=USER) == []


@pytest.mark.asyncio
async def test_reindex_missing_entry_raises(service: MemoryUserService) -> None:
    """reindex 不存在的条目 → 明确错误（不静默）。"""
    with pytest.raises(KeyError):
        await service.reindex(tenant_id=TENANT, user_id=USER, entry_id=99999)
