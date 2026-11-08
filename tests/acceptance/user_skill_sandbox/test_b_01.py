"""[B-01] workspace 元数据 → PVC 目录 → 真实 bwrap 执行。

真实边界：真实 PostgreSQL（``task.workspace`` / ``task.workspace_lease``）→ 真实
tenant/user/agent 目录 → 真实 bwrap 只绑定当前 Agent 目录到 ``/workspace``。
不 mock 数据库、文件系统或沙箱；macOS 无 bwrap 时使用带 bwrap 的 Linux 测试镜像。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from muad_agent_core.sandbox import WorkspaceLayout, WorkspaceStore
from muad_agent_runtime.infrastructure.models.workspace import Workspace, WorkspaceLease
from muad_agent_runtime.infrastructure.workspace_store import SqlWorkspaceMetadataStore
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.user_skill_sandbox import system_deps_support as support

pytestmark = pytest.mark.integration


def _layout(root: Path) -> WorkspaceLayout:
    return WorkspaceLayout(
        root=root,
        quota_bytes=500 * 1024 * 1024,
        user_quota_bytes=500 * 1024 * 1024,
        file_limit=5000,
        data_ttl_seconds=30 * 24 * 60 * 60,
    )


def _open_permissions(root: Path) -> None:
    """容器内以 UID 10001 运行 bwrap；让真实 PVC 子目录对该用户可写。"""
    for directory, dirnames, _filenames in os.walk(root):
        os.chmod(directory, 0o777)
        for name in dirnames:
            os.chmod(os.path.join(directory, name), 0o777)


def _run(image: str, workspace: Path, command: str) -> str:
    result = support.run_in_bwrap(
        image,
        ["sh", "-c", command],
        mounts=((workspace, "/mnt/workspace", "/workspace", "rw"),),
        chdir="/workspace",
    )
    assert result.returncode == 0, (
        f"workspace bwrap 执行失败: {command}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return result.stdout


async def _cleanup(session: AsyncSession, tenant_id: str) -> None:
    workspace_ids = select(Workspace.id).where(Workspace.tenant_id == tenant_id)
    await session.execute(delete(WorkspaceLease).where(WorkspaceLease.workspace_id.in_(workspace_ids)))
    await session.execute(delete(Workspace).where(Workspace.tenant_id == tenant_id))
    await session.commit()


async def test_b01_same_agent_keeps_data_and_isolates_outputs_per_execution(
    shared_tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同 Agent 两次执行读到同一 data 文件；outputs 每次执行独立。"""
    image = support.probe_image()
    tenant, user, agent = f"acc-b01-{uuid.uuid4()}", uuid.uuid4(), uuid.uuid4()
    store = WorkspaceStore(
        layout=_layout(shared_tmp_path / "workspaces"),
        metadata=SqlWorkspaceMetadataStore(lambda: session_factory),
    )
    try:
        first = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="exec-1",
            run_id="run-1",
            lease_sec=60,
        )
        _open_permissions(shared_tmp_path / "workspaces")
        _run(
            image,
            first.workspace_dir,
            "printf 'shared' > data/shared.txt; "
            "printf 'first' > outputs/exec-1/only-first.txt",
        )
        await store.release(first)
        assert (first.data_dir / "shared.txt").read_text(encoding="utf-8") == "shared"

        second = await store.acquire(
            tenant_id=tenant,
            user_id=str(user),
            agent_id=str(agent),
            execution_id="exec-2",
            run_id="run-2",
            lease_sec=60,
        )
        _open_permissions(shared_tmp_path / "workspaces")
        assert second.output_dir != first.output_dir
        output = _run(
            image,
            second.workspace_dir,
            "cat data/shared.txt; printf '\\n'; "
            "test ! -e outputs/exec-2/only-first.txt && echo second-clean; "
            "printf 'second' > outputs/exec-2/only-second.txt",
        )
        assert output.splitlines()[0] == "shared"
        assert "second-clean" in output
        assert (first.output_dir / "only-first.txt").is_file()
        assert (second.output_dir / "only-second.txt").is_file()
        await store.release(second)

        workspace = await session.scalar(select(Workspace).where(Workspace.tenant_id == tenant))
        assert workspace is not None
        leases = (
            await session.scalars(select(WorkspaceLease).where(WorkspaceLease.workspace_id == workspace.id))
        ).all()
        assert {lease.execution_id for lease in leases} == {"exec-1", "exec-2"}
        assert all(lease.is_deleted for lease in leases)
    finally:
        await _cleanup(session, tenant)


async def test_b01_other_agent_user_tenant_cannot_see_workspace(
    shared_tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """不同 Agent / user / tenant 目录互不可见；bwrap 只挂当前 Agent 目录。"""
    image = support.probe_image()
    tenant = f"acc-b01-{uuid.uuid4()}"
    other_tenant = f"acc-b01-other-{uuid.uuid4()}"
    user, other_user = uuid.uuid4(), uuid.uuid4()
    agent, other_agent = uuid.uuid4(), uuid.uuid4()
    store = WorkspaceStore(
        layout=_layout(shared_tmp_path / "workspaces"),
        metadata=SqlWorkspaceMetadataStore(lambda: session_factory),
    )
    try:
        base = await store.acquire(
            tenant_id=tenant, user_id=str(user), agent_id=str(agent), execution_id="base"
        )
        other_agent_lease = await store.acquire(
            tenant_id=tenant, user_id=str(user), agent_id=str(other_agent), execution_id="base"
        )
        other_user_lease = await store.acquire(
            tenant_id=tenant, user_id=str(other_user), agent_id=str(agent), execution_id="base"
        )
        other_tenant_lease = await store.acquire(
            tenant_id=other_tenant, user_id=str(user), agent_id=str(agent), execution_id="base"
        )
        _open_permissions(shared_tmp_path / "workspaces")
        _run(image, base.workspace_dir, "printf 'secret' > data/secret.txt")

        output = _run(
            image,
            base.workspace_dir,
            "ls /workspace; test ! -e /workspace/../" + other_tenant + " && echo no-sibling; "
            "test ! -e /mnt && echo no-mnt; cat data/secret.txt",
        )
        lines = output.splitlines()
        assert lines[:2] == ["data", "outputs"]
        assert "no-sibling" in lines
        assert "no-mnt" in lines
        assert lines[-1] == "secret"

        for lease in (other_agent_lease, other_user_lease, other_tenant_lease):
            hidden = _run(
                image,
                lease.workspace_dir,
                "test ! -e data/secret.txt && echo isolated",
            )
            assert "isolated" in hidden
            assert lease.output_dir != base.output_dir
            assert lease.workspace_dir != base.workspace_dir
    finally:
        await _cleanup(session, tenant)
        await _cleanup(session, other_tenant)


async def test_b01_workspace_root_does_not_touch_artifact_root(
    shared_tmp_path: Path,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """workspace 使用独立根目录；不读取/写入 artifacts 目录。"""
    artifact_root = shared_tmp_path / "artifact-root"
    artifact_root.mkdir()
    tenant = f"acc-b01-{uuid.uuid4()}"
    store = WorkspaceStore(
        layout=_layout(shared_tmp_path / "workspaces"),
        metadata=SqlWorkspaceMetadataStore(lambda: session_factory),
    )
    try:
        lease = await store.acquire(
            tenant_id=tenant,
            user_id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            execution_id="exec-artifact-free",
        )
        assert lease.workspace_dir.is_dir()
        assert list(artifact_root.iterdir()) == []
        assert "artifact-root" not in str(lease.workspace_dir)
    finally:
        await _cleanup(session, tenant)


# 保持 `sa` 与类型导入被 ruff 认为是真实使用（测试内显式断言表属于 task schema）。
def test_b01_workspace_tables_live_in_task_schema() -> None:
    assert Workspace.__table__.schema == "task"
    assert WorkspaceLease.__table__.schema == "task"
    assert isinstance(sa.select(Workspace.id), sa.sql.Select)
