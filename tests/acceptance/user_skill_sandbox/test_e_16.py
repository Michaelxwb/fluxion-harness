"""E-16: 清理失败可见、状态保留且下一轮可重试。

真实边界：Worker ``workspace.maintenance`` → 真实 PG 状态 → 真实 workspace 目录。
只注入文件删除失败（场景前置「模拟 PVC 删除失败」）；DB 认领、状态机、指标与重试
都是真实实现，不 mock ORM 或指标。
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

import pytest
from muad_agent_worker.metrics import WORKSPACE_CLEANUP_METRIC
from muad_agent_worker.worker.workspace_maintenance import WorkspaceMaintenanceLoop
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


def _failing_remover(_: Path) -> None:
    raise OSError("PVC delete failed")


@pytest.mark.asyncio
async def test_e16_failure_keeps_deleting_state_and_retries_next_round(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant = f"acc-e16-{uuid.uuid4()}"
    layout = make_layout(tmp_path / "workspaces")
    try:
        target = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=expired_at(31),
        )
        failed_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"})
        deleted_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"})

        failing = WorkspaceMaintenanceLoop(
            session_factory, layout, remover=_failing_remover
        )
        with caplog.at_level(
            logging.ERROR, logger="muad_agent_worker.worker.workspace_maintenance"
        ):
            first = await failing.run_once()

        assert first.failed == 1
        assert first.deleted == 0
        row = await fetch_workspace(session, target.id)
        assert row.state == "DELETING", "失败必须保留 DELETING 供下轮重试"
        assert row.is_deleted is False
        assert row.cleanup_error is not None
        assert "PVC delete failed" in row.cleanup_error
        assert target.directory.is_dir(), "删除失败不得假装目录已消失"
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"}) == failed_before + 1
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"}) == deleted_before
        failure_logs = [
            record
            for record in caplog.records
            if "workspace_cleanup_failed" in record.getMessage()
        ]
        assert failure_logs, "失败必须记 error 日志，不能静默吞掉"
        assert str(target.id) in str(failure_logs[0].__dict__.get("workspace_id", ""))

        retry = WorkspaceMaintenanceLoop(session_factory, layout, remover=shutil.rmtree)
        second = await retry.run_once()

        assert second.deleted == 1
        assert second.failed == 0
        retried = await fetch_workspace(session, target.id)
        assert retried.state == "DELETED"
        assert retried.is_deleted is True
        assert retried.cleanup_error is None
        assert not target.directory.exists()
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"}) == deleted_before + 1
    finally:
        await cleanup_tenant(session, tenant)
