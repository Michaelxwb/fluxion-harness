"""E-06: workspace 超过配额时拒绝执行，不静默截断/不写系统目录。

真实边界：``WorkspaceStore``（真实 PG 元数据）→ 真实 workspace 根目录 → 配额判定。
配额值来自配置（500 MiB / 500 MiB / 5,000 文件），测试用稀疏文件表达逻辑大小，
不 mock 文件系统或数据库；拒绝必须同时留下指标和 warning 日志。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest
from muad_agent_core.sandbox import (
    WorkspaceQuotaExceeded,
    WorkspaceQuotaPolicy,
    WorkspaceStore,
)
from muad_agent_runtime.infrastructure.workspace_store import SqlWorkspaceMetadataStore
from muad_agent_worker.metrics import (
    WORKSPACE_QUOTA_REJECTED_METRIC,
    record_workspace_quota_rejected,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .workspace_cleanup_support import (
    MIB,
    cleanup_tenant,
    make_layout,
    metric_value,
    write_sparse,
)

pytestmark = pytest.mark.integration


def _store(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    layout=None,
) -> tuple[WorkspaceStore, object]:
    resolved = layout or make_layout(tmp_path / "workspaces")
    metadata = SqlWorkspaceMetadataStore(lambda: session_factory)
    store = WorkspaceStore(
        layout=resolved,
        metadata=metadata,
        quota=WorkspaceQuotaPolicy.from_layout(resolved),
        usage=metadata,
        on_quota_rejected=record_workspace_quota_rejected,
    )
    return store, metadata


async def _active_lease_count(session: AsyncSession, execution_id: str) -> int:
    from muad_agent_worker.infrastructure.models.workspace import WorkspaceLease

    return int(
        await session.scalar(
            select(func.count())
            .select_from(WorkspaceLease)
            .where(
                WorkspaceLease.execution_id == execution_id,
                WorkspaceLease.is_deleted.is_(False),
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_e06_workspace_size_quota_rejects_without_truncating(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant = f"acc-e06-{uuid.uuid4()}"
    user, agent = uuid.uuid4(), uuid.uuid4()
    try:
        store, _ = _store(tmp_path, session_factory)
        seed = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="seed",
        )
        huge = seed.data_dir / "huge.bin"
        write_sparse(huge, 501 * MIB)
        await store.refresh_usage(seed)
        await store.release(seed)

        before = metric_value(WORKSPACE_QUOTA_REJECTED_METRIC)
        with caplog.at_level(logging.WARNING, logger="muad_agent_core.sandbox.workspace"):
            with pytest.raises(WorkspaceQuotaExceeded) as raised:
                await store.acquire(
                    tenant_id=tenant,
                    user_id=str(user),
                    agent_id=str(agent),
                    execution_id="rejected",
                )

        assert raised.value.code == "WORKSPACE_QUOTA_EXCEEDED"
        assert raised.value.reason == "workspace_size"
        assert huge.stat().st_size == 501 * MIB, "配额拒绝不得截断/改写已有数据"
        assert metric_value(WORKSPACE_QUOTA_REJECTED_METRIC) == before + 1
        assert any(
            "workspace_quota_exceeded" in record.getMessage() for record in caplog.records
        ), "拒绝必须留下可定位的 warning 日志"
        assert await _active_lease_count(session, "rejected") == 0
        # 所有创建路径都在显式 workspace 根内，没有落到系统目录/当前目录。
        assert str(tmp_path / "workspaces") in str(seed.workspace_dir)
        assert not (Path.cwd() / tenant).exists()
    finally:
        await cleanup_tenant(session, tenant)


@pytest.mark.asyncio
async def test_e06_file_count_quota_rejects(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = f"acc-e06-{uuid.uuid4()}"
    user, agent = uuid.uuid4(), uuid.uuid4()
    try:
        store, _ = _store(tmp_path, session_factory)
        seed = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="seed",
        )
        for index in range(5001):
            (seed.data_dir / f"file-{index}.txt").touch()
        await store.refresh_usage(seed)
        await store.release(seed)

        before = metric_value(WORKSPACE_QUOTA_REJECTED_METRIC)
        with pytest.raises(WorkspaceQuotaExceeded) as raised:
            await store.acquire(
                tenant_id=tenant,
                user_id=str(user),
                agent_id=str(agent),
                execution_id="rejected",
            )
        assert raised.value.code == "WORKSPACE_QUOTA_EXCEEDED"
        assert raised.value.reason == "workspace_files"
        assert metric_value(WORKSPACE_QUOTA_REJECTED_METRIC) == before + 1
        assert len(list(seed.data_dir.iterdir())) == 5001
    finally:
        await cleanup_tenant(session, tenant)


@pytest.mark.asyncio
async def test_e06_user_aggregate_quota_rejects_new_agent_workspace(
    tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = f"acc-e06-{uuid.uuid4()}"
    user = uuid.uuid4()
    try:
        store, _ = _store(tmp_path, session_factory)
        for agent in (uuid.uuid4(), uuid.uuid4()):
            lease = await store.acquire(
                tenant_id=tenant,
                user_id=str(user),
                agent_id=str(agent),
                execution_id="seed",
            )
            write_sparse(lease.data_dir / "archive.bin", 300 * MIB)
            await store.refresh_usage(lease)
            await store.release(lease)

        with pytest.raises(WorkspaceQuotaExceeded) as raised:
            await store.acquire(
                tenant_id=tenant,
                user_id=str(user),
                agent_id=str(uuid.uuid4()),
                execution_id="third-agent",
            )
        assert raised.value.code == "WORKSPACE_QUOTA_EXCEEDED"
        assert raised.value.reason == "user_size"
    finally:
        await cleanup_tenant(session, tenant)
