"""[B-110] 受控长期 Memory 读写（真实 PostgreSQL runtime.user_memory）。"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import UserMemory

USER_ONE = uuid.uuid4()
USER_TWO = uuid.uuid4()
TENANT = f"mem-{uuid.uuid4()}"
SOURCE_USER_EXPLICIT = "USER_EXPLICIT"
SOURCE_AGENT_INFERRED = "AGENT_INFERRED"


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with get_session_factory()() as session:
        await session.execute(UserMemory.__table__.delete().where(UserMemory.tenant_id == TENANT))
        await session.commit()


async def test_b110_write_and_read_with_tenant_user_isolation() -> None:
    """[B-110] 写入/读取；跨 tenant/user 无泄漏。"""
    service = MemoryService()
    entry = await service.upsert(
        tenant_id=TENANT,
        user_id=USER_ONE,
        category="PREFERENCE",
        memory_key="output.language",
        content_json={"value": "zh-CN"},
        source_type="EXPLICIT",
        source_ref="user-input",
    )
    assert entry["version"] == 1

    mine = await service.list_entries(TENANT, USER_ONE)
    assert len(mine) == 1
    assert mine[0]["memory_key"] == "output.language"

    other_user = await service.list_entries(TENANT, USER_TWO)
    assert other_user == []
    other_tenant = await service.list_entries(f"{TENANT}-other", USER_ONE)
    assert other_tenant == []


async def test_b110_update_bumps_version_not_duplicate() -> None:
    """[B-110] 同 key 更新走版本递增，不产生第二行。"""
    service = MemoryService()
    first = await service.upsert(
        tenant_id=TENANT, user_id=USER_ONE, category="PREFERENCE",
        memory_key="style.tone", content_json={"value": "formal"},
        source_type="EXPLICIT",
    )
    second = await service.upsert(
        tenant_id=TENANT, user_id=USER_ONE, category="PREFERENCE",
        memory_key="style.tone", content_json={"value": "concise"},
        source_type="EXPLICIT",
    )
    assert second["version"] == first["version"] + 1
    entries = await service.list_entries(TENANT, USER_ONE)
    assert len(entries) == 1
    assert entries[0]["content_json"] == {"value": "concise"}


async def test_b110_disabled_and_deleted_filtered() -> None:
    """[B-110] enabled=false 与软删除不读。"""
    service = MemoryService()
    await service.upsert(
        tenant_id=TENANT, user_id=USER_ONE, category="WORK_STYLE",
        memory_key="k.enabled", content_json={}, source_type="EXPLICIT",
    )
    disabled = await service.upsert(
        tenant_id=TENANT, user_id=USER_ONE, category="WORK_STYLE",
        memory_key="k.disabled", content_json={}, source_type="EXPLICIT",
    )
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(UserMemory)
            .where(UserMemory.id == uuid.UUID(disabled["id"]))
            .values(enabled=False)
        )
        await session.commit()

    keys = [e["memory_key"] for e in await service.list_entries(TENANT, USER_ONE)]
    assert "k.disabled" not in keys

    await service.disable(TENANT, USER_ONE, "k.enabled")  # 软删除
    assert await service.list_entries(TENANT, USER_ONE) == []


async def test_b110_categories_constrained() -> None:
    """[B-110] 仅 PREFERENCE/WORK_STYLE/EXPLICIT 受控写入。"""
    service = MemoryService()
    with pytest.raises(ValueError):
        await service.upsert(
            tenant_id=TENANT, user_id=USER_ONE, category="RUNTIME_FACT",
            memory_key="k", content_json={}, source_type="EXPLICIT",
        )


async def test_s02_same_key_overwrite_bumps_version_and_revives_enabled() -> None:
    """[S-02][integration] 同 key 二次写入：仍一行、`version+1`、新值落库、
    `update_time` 递增、`enabled` 复位 true。

    真实边界：Service → 真实 PostgreSQL（`runtime.user_memory`）。
    `enabled` 复位是 design §2.3.2 明确要求的覆盖语义：否则"重新记住"对一条被禁用的记忆不生效，
    而工具仍回执成功（用户被告知记住了、实际永不生效）。
    """
    service = MemoryService()
    first = await service.upsert(
        tenant_id=TENANT, user_id=USER_ONE, category="PREFERENCE",
        memory_key="reply.language", content_json={"value": "中文"},
        source_type=SOURCE_USER_EXPLICIT,
    )
    # 先禁用该行，再覆盖写入：必须复活
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(UserMemory)
            .where(UserMemory.id == uuid.UUID(first["id"]))
            .values(enabled=False)
        )
        await session.commit()

    second = await service.upsert(
        tenant_id=TENANT, user_id=USER_ONE, category="PREFERENCE",
        memory_key="reply.language", content_json={"value": "英文"},
        source_type=SOURCE_USER_EXPLICIT,
    )

    assert second["version"] == first["version"] + 1
    assert second["enabled"] is True
    assert second["content_json"] == {"value": "英文"}
    assert second["update_time"] > first["update_time"]

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == TENANT,
                    UserMemory.user_id == USER_ONE,
                    UserMemory.memory_key == "reply.language",
                )
            )
        ).scalars().all()
    assert len(rows) == 1  # 覆盖更新，不产生第二行
    assert rows[0].enabled is True
    assert rows[0].version == second["version"]


async def test_s02_concurrent_same_key_writes_leave_single_row() -> None:
    """[S-02][integration] 并发同 key 写入：每次调用都成功、仍只有一行、版本不丢。

    真实边界：Service → 真实 PostgreSQL（并发落在 partial unique 索引上）。
    两段式"先 select 再 insert/update"在并发下会撞唯一约束并抛错，故实现须走 upsert 冲突吸收。
    """
    service = MemoryService()
    results = await asyncio.gather(
        *[
            service.upsert(
                tenant_id=TENANT, user_id=USER_ONE, category="WORK_STYLE",
                memory_key="concurrent.key", content_json={"value": f"v{index}"},
                source_type=SOURCE_USER_EXPLICIT,
            )
            for index in range(5)
        ]
    )

    assert [item["memory_key"] for item in results] == ["concurrent.key"] * 5
    assert sorted(item["version"] for item in results) == [1, 2, 3, 4, 5]

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == TENANT,
                    UserMemory.user_id == USER_ONE,
                    UserMemory.memory_key == "concurrent.key",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].version == 5


async def test_injection_query_returns_only_recent_user_explicit_entries() -> None:
    """注入查询原语：只取 `USER_EXPLICIT` 且 `enabled` 未删除，按 `update_time DESC` 取最近 N 条。

    本原语是分级注入（RULE-05）在 SQL 层的落点：`AGENT_INFERRED` 不进自动注入，
    只能经 `recall`（`search`）取回。
    """
    service = MemoryService()
    for index, (key, source) in enumerate(
        [("a.first", SOURCE_USER_EXPLICIT), ("b.second", SOURCE_AGENT_INFERRED),
         ("c.third", SOURCE_USER_EXPLICIT)]
    ):
        await service.upsert(
            tenant_id=TENANT, user_id=USER_ONE, category="PREFERENCE",
            memory_key=key, content_json={"value": f"value-{index}"}, source_type=source,
        )

    injected = await service.list_for_injection(TENANT, USER_ONE, limit=10)
    assert [item["memory_key"] for item in injected] == ["c.third", "a.first"]

    limited = await service.list_for_injection(TENANT, USER_ONE, limit=1)
    assert [item["memory_key"] for item in limited] == ["c.third"]

    # 软删除后不再参与注入
    await service.disable(TENANT, USER_ONE, "c.third")
    remaining = await service.list_for_injection(TENANT, USER_ONE, limit=10)
    assert [item["memory_key"] for item in remaining] == ["a.first"]


async def test_search_filters_by_key_prefix_across_source_types() -> None:
    """检索原语（供 `recall` 用）：按 key 前缀过滤，**两类来源都可取回**，按 `update_time DESC`。"""
    service = MemoryService()
    for key, source in [
        ("reply.language", SOURCE_USER_EXPLICIT),
        ("reply.tone", SOURCE_AGENT_INFERRED),
        ("work.style", SOURCE_USER_EXPLICIT),
    ]:
        await service.upsert(
            tenant_id=TENANT, user_id=USER_ONE, category="PREFERENCE",
            memory_key=key, content_json={"value": key}, source_type=source,
        )

    prefixed = await service.search(TENANT, USER_ONE, prefix="reply.", limit=10)
    assert sorted(item["memory_key"] for item in prefixed) == ["reply.language", "reply.tone"]

    everything = await service.search(TENANT, USER_ONE, prefix=None, limit=10)
    assert sorted(item["memory_key"] for item in everything) == [
        "reply.language", "reply.tone", "work.style",
    ]

    capped = await service.search(TENANT, USER_ONE, prefix=None, limit=1)
    assert len(capped) == 1
