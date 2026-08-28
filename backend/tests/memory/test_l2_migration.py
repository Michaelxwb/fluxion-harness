"""TASK-009（phase2）L2 legacy 迁移验收测试（M202 dry-run 自动化）。

真实边界：真实 SQLite session_memory + personal_memory 表；不 mock。
幂等：二次执行零变更。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.memory.application.l2_migration import audit_l2, migrate_l2
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import personal_memory, session_memory


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store.engine
    finally:
        await store.close()


async def _seed_l2(engine: AsyncEngine, rows: list[tuple[str, str, str]]) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        for tenant_id, session_id, content in rows:
            await conn.execute(
                insert(session_memory).values(
                    tenant_id=tenant_id,
                    user_id="user-legacy",
                    session_id=session_id,
                    execution_id=f"exec-legacy-{session_id}",
                    level="l2",
                    role="user",
                    content=content,
                    tokens=len(content),
                    created_at=now,
                )
            )


@pytest.mark.asyncio
async def test_m202_dry_run_reports_and_migration_is_idempotent(engine: AsyncEngine) -> None:
    """dry-run 报告行数核对 → 迁移 → personal_memory 计数一致 → 幂等。"""
    rows = [
        ("tenant-a", "sess-1", "legacy 记录 1"),
        ("tenant-a", "sess-2", "legacy 记录 2"),
        ("tenant-b", "sess-3", "legacy 记录 3"),
    ]
    await _seed_l2(engine, rows)

    report = await audit_l2(engine)
    assert report.total == 3
    assert report.tenants == {"tenant-a": 2, "tenant-b": 1}

    result = await migrate_l2(engine)
    assert result.migrated == 3 and result.deleted == 3

    async with engine.connect() as conn:
        migrated = (
            await conn.execute(
                select(personal_memory.c.content).order_by(personal_memory.c.id)
            )
        ).scalars().all()
    assert len(migrated) == 3
    assert "legacy 记录 1" in migrated

    # 幂等：二次执行零变更
    after = await audit_l2(engine)
    assert after.total == 0
    again = await migrate_l2(engine)
    assert again.migrated == 0 and again.deleted == 0
    async with engine.connect() as conn:
        remaining = (await conn.execute(select(personal_memory.c.id))).scalars().all()
    assert len(remaining) == 3


@pytest.mark.asyncio
async def test_m202_empty_store_zero_migration(engine: AsyncEngine) -> None:
    report = await audit_l2(engine)
    assert report.total == 0
    result = await migrate_l2(engine)
    assert result.migrated == 0 and result.deleted == 0
