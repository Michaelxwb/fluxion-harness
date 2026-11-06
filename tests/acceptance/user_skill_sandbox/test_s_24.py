"""S-24: Worker ``workspace.maintenance`` TTL 清理过期目录并跳过活跃执行。

真实边界：真实 PostgreSQL ``task.workspace`` / ``task.workspace_lease`` → 真实 workspace
根目录。时间只从 PG ``now()`` 取值（测试注入的 ``now` 也写回 timestamptz）；``outputs`` 与
``data`` 随 workspace 的 30 天未访问 TTL 一起清理，活跃租约必须原样保留。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from muad_agent_worker.metrics import WORKSPACE_CLEANUP_METRIC
from muad_agent_worker.worker.workspace_maintenance import WorkspaceMaintenanceLoop
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .workspace_cleanup_support import (
    active_until,
    cleanup_tenant,
    expired_at,
    fetch_workspace,
    make_layout,
    metric_value,
    seed_workspace,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_s24_expired_workspace_is_deleted_and_active_lease_is_skipped(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = f"acc-s24-{uuid.uuid4()}"
    layout = make_layout(tmp_path / "workspaces")
    try:
        expired = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=expired_at(31),
        )
        active = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=expired_at(31),
            lease_until=active_until(10),
        )
        recent = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=datetime.now(UTC) - timedelta(days=1),
        )

        scanned_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "SCANNED"})
        deleted_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"})
        skipped_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "SKIPPED"})
        failed_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"})

        loop = WorkspaceMaintenanceLoop(session_factory, layout)
        summary = await loop.run_once()

        assert summary.scanned == 2, "过期 workspace + 活跃跳过各扫描一次"
        assert summary.deleted == 1
        assert summary.skipped == 1
        assert summary.failed == 0
        assert summary.released_bytes >= 0

        expired_row = await fetch_workspace(session, expired.id)
        assert expired_row.state == "DELETED"
        assert expired_row.is_deleted is True
        assert not expired.directory.exists(), "data 与 outputs 随 workspace TTL 一起删除"
        assert not expired.data_dir.exists()
        assert not expired.output_dir.exists()

        active_row = await fetch_workspace(session, active.id)
        assert active_row.state == "ACTIVE"
        assert active_row.is_deleted is False
        assert active.data_dir.is_dir() and active.output_dir.is_dir()
        assert (active.data_dir / "state.json").is_file()
        assert (active.output_dir / "result.json").is_file()

        recent_row = await fetch_workspace(session, recent.id)
        assert recent_row.state == "ACTIVE"
        assert recent.directory.is_dir()

        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "SCANNED"}) == scanned_before + 2
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"}) == deleted_before + 1
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "SKIPPED"}) == skipped_before + 1
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"}) == failed_before
    finally:
        await cleanup_tenant(session, tenant)


@pytest.mark.asyncio
async def test_ttl_cutoff_is_30_days_and_uses_postgres_timestamptz(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TTL 边界：31 天前删除、29 天前保留；时间列是带时区的 timestamptz。"""
    from muad_agent_worker.infrastructure.models.workspace import Workspace

    assert Workspace.__table__.c.last_access_time.type.timezone is True

    tenant = f"acc-s24-{uuid.uuid4()}"
    layout = make_layout(tmp_path / "workspaces")
    try:
        expired = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=datetime.now(UTC) - timedelta(days=31),
        )
        fresh = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=datetime.now(UTC) - timedelta(days=29),
        )
        loop = WorkspaceMaintenanceLoop(session_factory, layout)
        summary = await loop.run_once()

        assert summary.deleted == 1
        assert not expired.directory.exists()
        assert fresh.directory.is_dir()
        expired_row = await fetch_workspace(session, expired.id)
        fresh_row = await fetch_workspace(session, fresh.id)
        assert expired_row.state == "DELETED"
        assert fresh_row.state == "ACTIVE"
        assert fresh_row.last_access_time.tzinfo is not None
    finally:
        await cleanup_tenant(session, tenant)
