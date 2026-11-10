"""WorkspaceStore 元数据/租约/目录契约（真实 PostgreSQL + 真实文件系统）。

真实边界：``SqlWorkspaceMetadataStore`` → ``task.workspace`` / ``task.workspace_lease``
→ ``WorkspaceStore`` 创建 tenant/user/agent 目录。不 mock ORM 或数据库；不依赖 bwrap
（bwrap 路径由 B-01/S-11 覆盖）。
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from muad_agent_core.sandbox import (
    WorkspaceDeleting,
    WorkspaceLayout,
    WorkspaceStore,
    WorkspaceUnavailable,
    workspace_layout_from_settings,
)
from muad_agent_runtime.infrastructure.models.workspace import Workspace, WorkspaceLease
from muad_agent_runtime.infrastructure.workspace_store import SqlWorkspaceMetadataStore
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


def _layout(tmp_path) -> WorkspaceLayout:
    return WorkspaceLayout(
        root=tmp_path / "workspaces",
        quota_bytes=500 * 1024 * 1024,
        user_quota_bytes=500 * 1024 * 1024,
        file_limit=5000,
        data_ttl_seconds=30 * 24 * 60 * 60,
    )


def _store(
    tmp_path, session_factory: async_sessionmaker[AsyncSession], *, sandbox_mount: str | None = "/workspace"
) -> WorkspaceStore:
    return WorkspaceStore(
        layout=_layout(tmp_path),
        metadata=SqlWorkspaceMetadataStore(lambda: session_factory),
        sandbox_mount=sandbox_mount,
    )


async def _cleanup(session: AsyncSession, tenant_id: str) -> None:
    workspace_ids = select(Workspace.id).where(Workspace.tenant_id == tenant_id)
    await session.execute(delete(WorkspaceLease).where(WorkspaceLease.workspace_id.in_(workspace_ids)))
    await session.execute(delete(Workspace).where(Workspace.tenant_id == tenant_id))
    await session.commit()


@pytest.mark.asyncio
async def test_workspace_store_acquire_reuses_workspace_and_leases_per_execution(
    tmp_path, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """同 tenant+user+agent 复用 workspace；每次 execution 独立租约与 output 目录。"""
    tenant = f"acc-ws-{uuid.uuid4()}"
    user, agent = uuid.uuid4(), uuid.uuid4()
    try:
        store = _store(tmp_path, session_factory)
        first = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="exec-1",
            run_id="run-1",
            lease_sec=60,
        )
        second = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="exec-2",
            run_id="run-2",
            lease_sec=60,
        )

        assert first.workspace_id == second.workspace_id
        assert first.data_dir == second.data_dir == first.workspace_dir / "data"
        assert first.output_dir == first.workspace_dir / "outputs" / "exec-1"
        assert second.output_dir == first.workspace_dir / "outputs" / "exec-2"
        assert first.output_dir != second.output_dir
        assert first.data_dir.is_dir() and first.output_dir.is_dir()
        assert first.data_mount == "/workspace/data"
        assert first.output_mount == "/workspace/outputs/exec-1"

        workspace = await session.scalar(
            select(Workspace).where(Workspace.tenant_id == tenant)
        )
        assert workspace is not None
        assert workspace.state == "ACTIVE"
        assert workspace.last_access_time is not None
        leases = (
            await session.scalars(select(WorkspaceLease).where(WorkspaceLease.workspace_id == workspace.id))
        ).all()
        assert {lease.execution_id for lease in leases} == {"exec-1", "exec-2"}

        await store.release(first)
        await session.refresh(leases[0])
        released = (
            await session.scalars(
                select(WorkspaceLease).where(WorkspaceLease.execution_id == "exec-1")
            )
        ).one()
        assert released.is_deleted is True
        # workspace 行本身保留，data 跨执行持久。
        assert (await session.scalar(select(Workspace).where(Workspace.id == workspace.id))) is not None
    finally:
        await _cleanup(session, tenant)


@pytest.mark.asyncio
async def test_workspace_store_isolates_tenant_user_and_agent(
    tmp_path, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """tenant/user/agent 任一不同即不同目录；目录不会落进 artifacts 根。"""
    tenant = f"acc-ws-{uuid.uuid4()}"
    other_tenant = f"acc-ws-other-{uuid.uuid4()}"
    user, other_user = uuid.uuid4(), uuid.uuid4()
    agent, other_agent = uuid.uuid4(), uuid.uuid4()
    try:
        store = _store(tmp_path, session_factory)
        base = await store.acquire(
            tenant_id=tenant, user_id=str(user), agent_id=str(agent), execution_id="e1"
        )
        other_agent_lease = await store.acquire(
            tenant_id=tenant, user_id=str(user), agent_id=str(other_agent), execution_id="e1"
        )
        other_user_lease = await store.acquire(
            tenant_id=tenant, user_id=str(other_user), agent_id=str(agent), execution_id="e1"
        )
        other_tenant_lease = await store.acquire(
            tenant_id=other_tenant, user_id=str(user), agent_id=str(agent), execution_id="e1"
        )

        roots = {
            base.workspace_dir,
            other_agent_lease.workspace_dir,
            other_user_lease.workspace_dir,
            other_tenant_lease.workspace_dir,
        }
        assert len(roots) == 4
        assert base.workspace_dir.is_dir()
        assert base.workspace_dir != base.workspace_dir.parent
    finally:
        await _cleanup(session, tenant)
        await _cleanup(session, other_tenant)


@pytest.mark.asyncio
async def test_workspace_store_rejects_deleting_workspace(
    tmp_path, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """DELETING/DELETED 拒绝新执行，不自动恢复为 ACTIVE。"""
    tenant = f"acc-ws-{uuid.uuid4()}"
    user, agent = uuid.uuid4(), uuid.uuid4()
    try:
        store = _store(tmp_path, session_factory)
        leased = await store.acquire(
            tenant_id=tenant, user_id=str(user), agent_id=str(agent), execution_id="before"
        )
        await store.release(leased)
        workspace = await session.scalar(select(Workspace).where(Workspace.id == leased.workspace_id))
        assert workspace is not None
        workspace.state = "DELETING"
        await session.commit()

        with pytest.raises(WorkspaceDeleting) as raised:
            await store.acquire(
                tenant_id=tenant, user_id=str(user), agent_id=str(agent), execution_id="during"
            )
        assert raised.value.code == "WORKSPACE_DELETING"
        await session.refresh(workspace)
        assert workspace.state == "DELETING"
        assert (
            await session.scalar(
                select(WorkspaceLease).where(
                    WorkspaceLease.workspace_id == workspace.id,
                    WorkspaceLease.execution_id == "during",
                )
            )
        ) is None
    finally:
        await _cleanup(session, tenant)


@pytest.mark.asyncio
async def test_workspace_store_rejects_path_components_and_empty_root(
    tmp_path, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """路径穿越/空 root 显式失败，绝不回落到 cwd 或根目录。"""
    tenant = f"acc-ws-{uuid.uuid4()}"
    store = _store(tmp_path, session_factory)
    try:
        with pytest.raises(WorkspaceUnavailable):
            await store.acquire(
                tenant_id=tenant,
                user_id=str(uuid.uuid4()),
                agent_id=str(uuid.uuid4()),
                execution_id="../escape",
            )
    finally:
        await _cleanup(session, tenant)

    with pytest.raises(ValueError, match="workspace root"):
        WorkspaceLayout(
            root=Path(""),
            quota_bytes=1,
            user_quota_bytes=1,
            file_limit=1,
            data_ttl_seconds=1,
        )
    with pytest.raises(ValueError, match="workspace root"):
        WorkspaceLayout(
            root=Path("/"),
            quota_bytes=1,
            user_quota_bytes=1,
            file_limit=1,
            data_ttl_seconds=1,
        )
    with pytest.raises(ValueError, match="workspace_root"):
        workspace_layout_from_settings(SimpleNamespace(workspace_root=""))
    assert not (tmp_path / "escape").exists()


@pytest.mark.asyncio
async def test_workspace_store_empty_sandbox_mount_uses_host_paths(
    tmp_path, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """dev-unsafe 显式关闭 /workspace 映射时，注入宿主可见路径（仍不越出 workspace）。"""
    tenant = f"acc-ws-{uuid.uuid4()}"
    try:
        store = _store(tmp_path, session_factory, sandbox_mount=None)
        lease = await store.acquire(
            tenant_id=tenant,
            user_id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            execution_id="dev-exec",
        )
        assert lease.data_mount == str(lease.data_dir)
        assert lease.output_mount == str(lease.output_dir)
        assert lease.data_mount.startswith(str(tmp_path))
    finally:
        await _cleanup(session, tenant)


async def test_prepare_cancellation_holds_lease_until_filesystem_work_finishes(
    tmp_path: Path, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, execution = f"acc-prepare-{uuid.uuid4()}", str(uuid.uuid4())
    started, finish = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def prepare(root: Path) -> None:
        loop.call_soon_threadsafe(started.set)
        if not finish.wait(timeout=5):
            raise TimeoutError("prepare diagnostic was not released")
        root.mkdir(parents=True, exist_ok=True)

    task = asyncio.create_task(_store(tmp_path, session_factory).acquire(
        tenant_id=tenant, user_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()),
        execution_id=execution, prepare=prepare,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        task.cancel()
        await asyncio.sleep(0)
        lease = await session.scalar(select(WorkspaceLease).where(
            WorkspaceLease.execution_id == execution,
        ))
        assert lease is not None and lease.is_deleted is False
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        await session.refresh(lease)
        assert lease.is_deleted is True
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await _cleanup(session, tenant)
