"""Worker 内置 ``workspace.maintenance`` 循环（harness-worker RULE-worker-001）。

真实边界：真实 PostgreSQL + 真实 workspace 目录；文件删除只注入一次失败来验证
「失败可见且下一轮重试」，不 mock DB、指标或循环本身。
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from muad_agent_core.sandbox import WorkspaceQuotaPolicy, WorkspaceStore
from muad_agent_core.skill import SkillExecutionResult, SkillExecutionStatus
from muad_agent_worker.infrastructure.models.workspace import Workspace, WorkspaceLease
from muad_agent_worker.infrastructure.workspace_store import WorkerWorkspaceMetadataStore
from muad_agent_worker.metrics import WORKSPACE_CLEANUP_METRIC
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.workspace_maintenance import WorkspaceMaintenanceLoop
from muad_contracts.platform_settings import default_platform_settings
from sqlalchemy import select, update

from agent_worker.conftest import TenantContext
from agent_worker.helpers import persist_task
from tests.acceptance.user_skill_sandbox.workspace_cleanup_support import (
    cleanup_tenant,
    expired_at,
    fetch_workspace,
    make_layout,
    metric_value,
    seed_workspace,
)


class _Resolver:
    def __init__(self, ready_dir: Path) -> None:
        self.ready_dir = ready_dir

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        return self.ready_dir


class _RecordingSkillExecutor:
    def __init__(self) -> None:
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        return SkillExecutionResult(
            status=SkillExecutionStatus.SUCCEEDED,
            result={"ok": True},
            exit_code=0,
            workspace_dir=request.workspace_dir,
        )



#: Snapshot budget must freeze the per-execution timeout (v3); fixtures use the schema default.
_TIMEOUT_SEC = default_platform_settings().task.execution_timeout_sec


def _snapshot(artifact_id: uuid.UUID, checksum: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent": {"key": "agent"},
        "model": {"key": "model"},
        "skills": [
            {
                "skill_id": str(uuid.uuid4()),
                "artifact_id": str(artifact_id),
                "key": "policy_check",
                "version": "1",
                "checksum": checksum,
                "storage_key": "skills/policy-check.zip",
                "execution_mode": "ASYNC",
            }
        ],
        "mcp": [],
        "prompt_template_version": "v1",
        "budget": {"max_tokens": 1024, "execution_timeout_sec": _TIMEOUT_SEC},
    }


@pytest.mark.asyncio
async def test_maintenance_forever_survives_failure_and_retries(
    tmp_path: Path,
    tenant: TenantContext,
) -> None:
    layout = make_layout(tmp_path / "workspaces")
    maintenance_tenant = f"test-ws-{uuid.uuid4()}"
    factory = tenant.session_factory
    try:
        async with factory() as session:
            target = await seed_workspace(
                session,
                layout,
                tenant_id=maintenance_tenant,
                last_access_time=expired_at(31),
            )
        calls = {"count": 0}

        def flaky_remover(path: Path) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("first tick fails")
            shutil.rmtree(path)

        failed_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"})
        deleted_before = metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"})
        loop = WorkspaceMaintenanceLoop(
            factory, layout, interval_sec=0.05, remover=flaky_remover
        )
        task = asyncio.create_task(loop.run_forever(), name="test.workspace.maintenance")
        state = "DELETING"
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                async with factory() as session:
                    row = await session.scalar(
                        select(Workspace).where(Workspace.id == target.id)
                    )
                    assert row is not None
                    state = row.state
                if state == "DELETED":
                    break
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        assert state == "DELETED", "维护循环在失败后必须继续下一轮重试"
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"}) >= failed_before + 1
        assert metric_value(WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"}) == deleted_before + 1
    finally:
        async with factory() as session:
            await cleanup_tenant(session, maintenance_tenant)


@pytest.mark.asyncio
async def test_workspace_maintenance_is_disabled_when_root_is_not_mounted(
    tmp_path: Path,
    tenant: TenantContext,
) -> None:
    """PVC 根未挂载时不得启用清理（PRD：根卷必须同时挂 Runtime/Worker）。"""
    from muad_agent_worker.main import _workspace_maintenance
    from muad_common import SharedSettings

    missing = SharedSettings(workspace_root=str(tmp_path / "missing"))
    assert _workspace_maintenance(missing, tenant.session_factory) is None

    present_root = tmp_path / "present"
    present_root.mkdir()
    present = SharedSettings(workspace_root=str(present_root))
    loop = _workspace_maintenance(present, tenant.session_factory)
    assert loop is not None


@pytest.mark.asyncio
async def test_skill_executor_acquires_workspace_lease_and_updates_last_access(
    tmp_path: Path,
    tenant: TenantContext,
) -> None:
    """Worker 执行 Skill 前 acquire 租约并 touch last_access，执行后 refresh/release。"""
    layout = make_layout(tmp_path / "workspaces")
    ready = tmp_path / "ready"
    ready.mkdir()
    metadata = WorkerWorkspaceMetadataStore(lambda: tenant.session_factory)
    store = WorkspaceStore(
        layout=layout,
        metadata=metadata,
        quota=WorkspaceQuotaPolicy.from_layout(layout),
        usage=metadata,
    )
    skill_executor = _RecordingSkillExecutor()
    artifact_id = uuid.uuid4()
    task = await persist_task(
        tenant,
        skill_artifact_id=artifact_id,
        execution_snapshot_json=_snapshot(artifact_id, "sha256:" + "2" * 64),
        input_json={"customers": ["A"]},
    )
    executor = SkillTaskExecutor(
        _Resolver(ready), skill_executor, workspace_store=store
    )

    first = await executor.execute(task)
    assert first["status"] == "SUCCEEDED"
    assert len(skill_executor.requests) == 1
    request = skill_executor.requests[0]
    workspace_dir = layout.workspace_dir(
        tenant_id=task.tenant_id,
        user_id=str(task.actor_user_id),
        agent_id=str(task.agent_id),
    )
    assert request.workspace_dir == workspace_dir
    assert (workspace_dir / "data").is_dir()
    assert (workspace_dir / "outputs" / str(task.id)).is_dir()

    async with tenant.session_factory() as session:
        workspace = await session.scalar(
            select(Workspace).where(
                Workspace.tenant_id == task.tenant_id,
                Workspace.user_id == task.actor_user_id,
                Workspace.agent_id == task.agent_id,
            )
        )
        assert workspace is not None
        first_access = workspace.last_access_time
        old = datetime.now(UTC) - timedelta(days=15)
        await session.execute(
            update(Workspace)
            .where(Workspace.id == workspace.id)
            .values(last_access_time=old)
        )
        await session.commit()

    second = await executor.execute(task)
    assert second["status"] == "SUCCEEDED"
    async with tenant.session_factory() as session:
        refreshed = await fetch_workspace(session, workspace.id)
        assert refreshed.last_access_time > old, "执行必须更新 last_access_time"
        lease = await session.scalar(
            select(WorkspaceLease).where(WorkspaceLease.execution_id == str(task.id))
        )
        assert lease is not None
        assert lease.is_deleted is True, "执行结束必须释放租约"
    assert first_access.tzinfo is not None
