"""B-02: 多副本清理只有一个副本认领成功，另一个跳过且不重复计数。

真实边界：两个独立 ``WorkspaceMaintenanceLoop``（各自 session）→ 同一 PostgreSQL →
同一 workspace 根目录。认领使用行锁 + 会话级 advisory lock，删除期间第二个副本不得
再次删除或重复记 DELETED。
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path

import pytest
from muad_agent_worker.metrics import WORKSPACE_CLEANUP_METRIC
from muad_agent_worker.worker.workspace_maintenance import WorkspaceMaintenanceLoop
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .workspace_cleanup_support import (
    cleanup_tenant,
    expired_at,
    fetch_workspace,
    make_layout,
    metric_value,
    seed_workspace,
)

pytestmark = pytest.mark.integration


def _slow_remover(path: Path) -> None:
    time.sleep(0.3)
    shutil.rmtree(path)


@pytest.mark.asyncio
async def test_b02_two_replicas_delete_once(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = f"acc-b02-{uuid.uuid4()}"
    layout = make_layout(tmp_path / "workspaces")
    try:
        target = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=expired_at(31),
        )
        deleted_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"})
        replica_a = WorkspaceMaintenanceLoop(
            session_factory, layout, remover=_slow_remover
        )
        replica_b = WorkspaceMaintenanceLoop(
            session_factory, layout, remover=_slow_remover
        )

        first, second = await asyncio.gather(
            replica_a.run_once(), replica_b.run_once()
        )
        summaries = (first, second)

        assert sum(item.deleted for item in summaries) == 1, "只能有一个副本执行删除"
        assert sum(item.claimed for item in summaries) == 1, "同一 workspace 只能被认领一次"
        assert sorted(item.deleted for item in summaries) == [0, 1]
        assert not target.directory.exists()
        row = await fetch_workspace(session, target.id)
        assert row.state == "DELETED"
        assert row.is_deleted is True

        rows = int(
            await session.scalar(
                select(func.count())
                .select_from(type(row))
                .where(type(row).tenant_id == tenant)
            )
            or 0
        )
        assert rows == 1, "并发清理不得重复插入/重复删除 workspace 行"
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"}) == (
            deleted_before + 1
        ), "删除计数只允许 +1"
    finally:
        await cleanup_tenant(session, tenant)
