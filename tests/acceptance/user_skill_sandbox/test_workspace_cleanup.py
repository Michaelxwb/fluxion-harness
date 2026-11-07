"""Workspace TTL/配额/用量扫描的 Spec verifier 用例。

``harness-time#RULE-time-001`` 与 ``harness-worker#RULE-worker-001`` 要求：
- TTL 以带时区的 30 天为边界，不使用 NFS atime；
- 活跃租约跳过；
- 配额覆盖单 workspace、单用户聚合和文件数；
- 用量扫描不跟随 symlink，超限时快速停止（不无限遍历）。

真实边界：真实 PostgreSQL、真实文件系统；扫描器与策略是纯函数，但由真实目录驱动。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from muad_agent_core.sandbox import (
    WorkspaceQuotaExceeded,
    WorkspaceQuotaPolicy,
    WorkspaceStore,
    scan_workspace_usage,
)
from muad_agent_runtime.infrastructure.workspace_store import SqlWorkspaceMetadataStore
from muad_agent_worker.infrastructure.models.workspace import Workspace
from muad_agent_worker.worker.workspace_maintenance import WorkspaceMaintenanceLoop
from muad_common import SharedSettings
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .workspace_cleanup_support import (
    MIB,
    cleanup_tenant,
    fetch_workspace,
    make_layout,
    seed_workspace,
    write_sparse,
)

pytestmark = pytest.mark.integration


def test_workspace_quota_defaults_are_500mib_5000_files_and_30_days() -> None:
    fields = SharedSettings.model_fields
    assert fields["workspace_quota_mib"].default == 500
    assert fields["workspace_user_quota_mib"].default == 500
    assert fields["workspace_file_limit"].default == 5000
    assert fields["workspace_output_ttl_days"].default == 30
    assert fields["workspace_data_ttl_days"].default == 30

    policy = WorkspaceQuotaPolicy.from_layout(make_layout(Path("/tmp/workspace-layout-check")))
    assert policy.workspace_bytes == 500 * MIB
    assert policy.user_bytes == 500 * MIB
    assert policy.file_limit == 5000


def test_workspace_usage_scan_does_not_follow_symlink_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    data = root / "data"
    data.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    write_sparse(outside, 600 * MIB)
    (data / "outside-link").symlink_to(outside)

    usage = scan_workspace_usage(root, file_limit=5000)

    assert usage.file_count == 1
    assert usage.size_bytes < MIB, "symlink 不能把根外大文件计入 workspace 配额"
    assert usage.truncated is False


def test_workspace_usage_scan_stops_after_file_limit(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    data = root / "data"
    data.mkdir(parents=True)
    for index in range(5001):
        (data / f"file-{index}.txt").touch()

    usage = scan_workspace_usage(root, file_limit=5000)

    assert usage.truncated is True
    assert usage.file_count == 5001


@pytest.mark.asyncio
async def test_ttl_uses_injected_postgres_reference_time(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TTL 截止时间来自调用方注入的 timezone-aware 时间，不读 NFS atime。"""
    tenant = f"acc-ttl-{uuid.uuid4()}"
    layout = make_layout(tmp_path / "workspaces")
    try:
        target = await seed_workspace(
            session,
            layout,
            tenant_id=tenant,
            last_access_time=datetime.now(UTC) - timedelta(days=25),
        )
        loop = WorkspaceMaintenanceLoop(session_factory, layout)
        reference = datetime.now(UTC) + timedelta(days=10)
        summary = await loop.run_once(now=reference)

        assert summary.deleted == 1
        assert not target.directory.exists()
        row = await fetch_workspace(session, target.id)
        assert row.state == "DELETED"
        assert row.last_access_time.tzinfo is not None
    finally:
        await cleanup_tenant(session, tenant)


@pytest.mark.asyncio
async def test_last_access_time_is_touched_by_workspace_acquire(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = f"acc-ttl-{uuid.uuid4()}"
    user, agent = uuid.uuid4(), uuid.uuid4()
    layout = make_layout(tmp_path / "workspaces")
    try:
        store = WorkspaceStore(
            layout=layout,
            metadata=SqlWorkspaceMetadataStore(lambda: session_factory),
        )
        lease = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="first",
        )
        old = datetime.now(UTC) - timedelta(days=15)
        await session.execute(
            update(Workspace)
            .where(Workspace.id == lease.workspace_id)
            .values(last_access_time=old)
        )
        await session.commit()
        await store.release(lease)

        second = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="second",
        )
        await store.release(second)
        await session.commit()
        row = await fetch_workspace(session, lease.workspace_id)
        assert row.last_access_time > old
    finally:
        await cleanup_tenant(session, tenant)


@pytest.mark.asyncio
async def test_workspace_quota_rejects_usage_above_limits(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """策略边界：超过（不是等于）500 MiB / 5,000 文件才拒绝，且沿用统一错误码。"""
    tenant = f"acc-quota-{uuid.uuid4()}"
    user, agent = uuid.uuid4(), uuid.uuid4()
    layout = make_layout(tmp_path / "workspaces", file_limit=3)
    try:
        store = WorkspaceStore(
            layout=layout,
            metadata=SqlWorkspaceMetadataStore(lambda: session_factory),
            quota=WorkspaceQuotaPolicy.from_layout(layout),
            usage=SqlWorkspaceMetadataStore(lambda: session_factory),
        )
        lease = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="seed",
        )
        (lease.data_dir / "a").write_text("a", encoding="utf-8")
        (lease.data_dir / "b").write_text("b", encoding="utf-8")
        (lease.data_dir / "c").write_text("c", encoding="utf-8")
        (lease.data_dir / "d").write_text("d", encoding="utf-8")
        await store.refresh_usage(lease)
        await store.release(lease)

        with pytest.raises(WorkspaceQuotaExceeded) as raised:
            await store.acquire(
                tenant_id=tenant,
                user_id=str(user),
                agent_id=str(agent),
                execution_id="over",
            )
        assert raised.value.code == "WORKSPACE_QUOTA_EXCEEDED"
        assert raised.value.reason == "workspace_files"
    finally:
        await cleanup_tenant(session, tenant)
