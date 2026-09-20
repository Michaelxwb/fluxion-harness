"""[B-110] 受控长期 Memory 读写（真实 PostgreSQL runtime.user_memory）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import UserMemory

USER_ONE = uuid.uuid4()
USER_TWO = uuid.uuid4()
TENANT = f"mem-{uuid.uuid4()}"


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
