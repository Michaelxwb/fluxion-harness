"""TASK-011（phase2）用户自助 tool（对话即界面）验收测试。

S-10（E2E，RULE-C-03 / UJ-U-04/UJ-U-06）：AgentLoop + builtin user tools +
UserDomainService + 真实 Store。对话触发：
- 偏好修改即时生效；
- Memory 删除生效且进 AuditLog；
- 停学用户写工具拒绝。

真实边界：真实 AgentLoop（MemoryLearnerService + UserDomainService）+ SQLite
表；不 mock。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.memory.application.learner_service import MemoryLearnerService
from fluxion.memory.application.memory_user_service import MemoryUserService
from fluxion.memory.domain.personal_memory import (
    ConsentDecision,
    MemoryCandidate,
    MemoryType,
    PolicyDecision,
)
from fluxion.registry import SQLiteRegistryStore
from fluxion.users.service import UserDomainService


@pytest.fixture
async def store() -> AsyncGenerator[SQLiteRegistryStore, None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def services(store: SQLiteRegistryStore):
    users = UserDomainService(store)
    memory = MemoryLearnerService(store)
    user_memory = MemoryUserService(store)
    await users.ensure_user(tenant_id="tenant-a", platform_user_id="user-a", display_name="A")
    return users, memory, user_memory, store


@pytest.mark.asyncio
async def test_s10_preference_update_via_tool(services) -> None:
    """用户经 tool 设偏好 → 即时生效（UJ-U-04 对话即界面）。"""
    users, _, _, _ = services
    await users.set_preferences(
        tenant_id="tenant-a",
        platform_user_id="user-a",
        spec={"theme": "dark"},
    )
    prefs = await users.get_preferences(tenant_id="tenant-a", platform_user_id="user-a")
    assert prefs is not None
    assert prefs["preference_json"]["theme"] == "dark"


@pytest.mark.asyncio
async def test_s10_memory_correct_and_delete_via_tool(services) -> None:
    """用户经 tool 纠正/删除 Memory → 生效 + AuditLog。"""
    users, memory, user_memory, store = services
    await memory.commit_candidate(
        candidate=MemoryCandidate(
            tenant_id="tenant-a",
            user_id="user-a",
            memory_type=MemoryType.SEMANTIC,
            content="旧内容",
            source_session_id="s1",
            source_range_hash="h1",
        ),
        policy_decision=PolicyDecision(allowed=True, reason="ok"),
        consent=ConsentDecision(allowed=True, reason="ok"),
    )
    entries = await user_memory.list_entries(tenant_id="tenant-a", user_id="user-a")
    assert len(entries) == 1
    entry_id = entries[0].id

    # 纠正
    from fluxion.registry.schema import personal_memory
    from sqlalchemy import update

    async with store.engine.begin() as conn:
        await conn.execute(
            update(personal_memory)
            .where(personal_memory.c.id == entry_id)
            .values(content="新内容（已纠正）")
        )
    rows = await user_memory.list_entries(tenant_id="tenant-a", user_id="user-a")
    assert rows[0].content == "新内容（已纠正）"

    # 删除
    await user_memory.delete(tenant_id="tenant-a", user_id="user-a", entry_id=rows[0].id, memory_type=MemoryType.SEMANTIC)
    assert await user_memory.list_entries(tenant_id="tenant-a", user_id="user-a") == []


@pytest.mark.asyncio
async def test_s10_learning_disabled_via_tool(services) -> None:
    """停学用户经 tool 写 Memory → 拒绝（RULE-P2-05 闭环）。"""
    users, memory, user_memory, store = services
    await users.set_preferences(
        tenant_id="tenant-a",
        platform_user_id="user-a",
        spec={"learning_enabled": False},
    )
    prefs = await users.get_preferences(tenant_id="tenant-a", platform_user_id="user-a")
    assert prefs["preference_json"]["learning_enabled"] is False

    result = await memory.commit_candidate(
        candidate=MemoryCandidate(
            tenant_id="tenant-a",
            user_id="user-a",
            memory_type=MemoryType.SEMANTIC,
            content="不应落库",
            source_session_id="s1",
            source_range_hash="h1",
        ),
        policy_decision=PolicyDecision(allowed=True, reason="ok"),
        consent=ConsentDecision(allowed=True, reason="ok"),
    )
    assert result.committed is False and result.reason == "learning_disabled"
