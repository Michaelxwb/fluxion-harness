"""TASK-031 · Task 产物保留清理（design §3.1.5 owner、§3.5.11 保留）。

真实边界：真实 PostgreSQL（`task.task_execution` 与 `runtime.artifact` 都是真实行）、真实共享
存储（`NfsArtifactStore` 落临时目录，不 mock）、真实清理服务与真实不可变版本解析。

两条硬事实：

- **三类等待态都受保护**：`WAITING_EXTERNAL` / `WAITING_HUMAN` 与运行中一样还要恢复材料；
  只认旧的单一 `WAITING` 会把它们的产物删掉（Console 产物清理与 Runtime Task 产物清理同一口径）。
- **删历史不伪装成功**：文件与元数据分开、失败留行可重试；清理后下载按"不存在"处理，不拿
  别的版本顶替，也不泄露存在性。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from muad_agent_runtime.api.deps import get_artifact_store
from muad_agent_runtime.application.attachments.task_artifacts import resolve_task_artifact_version
from muad_agent_runtime.application.task_artifact_retention import (
    TaskArtifactRetentionReport,
    run_task_artifact_retention,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord
from muad_agent_runtime.main import app
from muad_api import AppError
from muad_artifact_store import NfsArtifactStore

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
RETENTION_DAYS = 90
PURGE_ORDER = ("artifact", *CLEANUP_ORDER)


async def _purge(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for table in PURGE_ORDER:
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.execute(
            sa.text("DELETE FROM task.task_execution WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await session.commit()


@pytest.fixture()
async def task_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge(tenant.tenant_id)


@pytest.fixture()
def artifact_root(tmp_path: Path) -> AsyncIterator[Path]:
    app.dependency_overrides[get_artifact_store] = lambda: NfsArtifactStore(tmp_path)
    try:
        yield tmp_path
    finally:
        app.dependency_overrides.pop(get_artifact_store, None)


async def _seed_execution(
    tenant: TenantContext,
    *,
    status: str,
    age_days: float = 120,
    finished: bool = True,
    tenant_id: str | None = None,
) -> uuid.UUID:
    execution_id = uuid.uuid4()
    created = NOW - timedelta(days=age_days)
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO task.task_execution"
                " (id, tenant_id, agent_id, actor_user_id, trigger_type, status, input_json,"
                "  execution_snapshot_json, snapshot_hash, idempotency_key, create_time,"
                "  update_time, finished_at)"
                " VALUES (:id, :tenant_id, :agent_id, :actor_user_id, 'SCHEDULED', :status,"
                "  '{}'::jsonb, '{}'::jsonb, :snapshot_hash, :idempotency_key, :created,"
                "  :created, :finished_at)"
            ),
            {
                "id": execution_id,
                "tenant_id": tenant_id or tenant.tenant_id,
                "agent_id": tenant.agent_id,
                "actor_user_id": tenant.platform_user_id,
                "status": status,
                "snapshot_hash": "sha256:" + "e" * 64,
                "idempotency_key": f"exec-{execution_id}",
                "created": created,
                "finished_at": created + timedelta(minutes=1) if finished else None,
            },
        )
        await session.commit()
    return execution_id


async def _seed_artifact(
    tenant: TenantContext,
    store: NfsArtifactStore,
    *,
    task_id: uuid.UUID | None,
    age_days: float = 120,
    data: bytes = b"report",
    tenant_id: str | None = None,
) -> uuid.UUID:
    """一个 Task-owned（或 Run-owned）产物行 + 盘上字节；版本历史按 §3.4 的形状存在 metadata。"""
    artifact_id = uuid.uuid4()
    key = f"tasks/{tenant_id or tenant.tenant_id}/{task_id or 'run'}/{artifact_id}/v1"
    store.write(key, data)
    created = NOW - timedelta(days=age_days)
    async with get_session_factory()() as session:
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant_id or tenant.tenant_id,
                run_id=None,
                task_id=task_id,
                agent_id=tenant.agent_id,
                user_id=tenant.platform_user_id,
                artifact_type="TASK_OUTPUT" if task_id else "TOOL_RESULT",
                storage_key=key,
                media_type="text/markdown",
                size=len(data),
                checksum="sha256:" + hashlib.sha256(data).hexdigest(),
                metadata_json={"filename": "report.md", "version": 1, "versions": []},
                create_time=created,
                update_time=created,
            )
        )
        await session.commit()
    return artifact_id


async def _seed_run_artifact(
    tenant: TenantContext, store: NfsArtifactStore, *, data: bytes = b"run-report"
) -> uuid.UUID:
    """Run-owned 产物（`run_id` 非空、`task_id` 为空）：任务保留清理不该碰它。"""
    conversation_id, run_id, artifact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = f"runs/{tenant.tenant_id}/{run_id}/{artifact_id}"
    store.write(key, data)
    created = NOW - timedelta(days=200)
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=0,
            )
        )
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status="COMPLETED",
                input_text="seed",
                trace_id=f"trace-{run_id}",
            )
        )
        await session.flush()
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                task_id=None,
                agent_id=tenant.agent_id,
                user_id=tenant.platform_user_id,
                artifact_type="TOOL_RESULT",
                storage_key=key,
                media_type="text/markdown",
                size=len(data),
                checksum="sha256:" + hashlib.sha256(data).hexdigest(),
                metadata_json={},
                create_time=created,
                update_time=created,
            )
        )
        await session.commit()
    return artifact_id


async def _artifact_rows(tenant: TenantContext) -> list[dict[str, object]]:
    async with get_session_factory()() as session:
        result = await session.execute(
            sa.text(
                "SELECT id, task_id, storage_key FROM runtime.artifact"
                " WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant.tenant_id},
        )
        return [dict(row) for row in result.mappings()]


async def _retention(
    tenant: TenantContext, root: Path, *, batch_size: int = 200
) -> TaskArtifactRetentionReport:
    return await run_task_artifact_retention(
        get_session_factory(),
        artifact_root=root,
        tenant_id=tenant.tenant_id,
        execution_history_days=RETENTION_DAYS,
        batch_size=batch_size,
        now=NOW,
    )


# --------------------------------------------------------------------- cases


async def test_console_retention_protects_every_waiting_status(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    """硬验收点：只把 `WAITING` 当活动执行会让等待中执行的产物被误删。"""
    from muad_console_platform.infrastructure.repositories.artifact_retention_repository import (
        ArtifactRetentionRepository,
    )

    store = NfsArtifactStore(artifact_root)
    completed = await _seed_execution(task_tenant, status="COMPLETED")
    waiting_external = await _seed_execution(
        task_tenant, status="WAITING_EXTERNAL", finished=False
    )
    waiting_human = await _seed_execution(task_tenant, status="WAITING_HUMAN", finished=False)
    artifacts = {
        "completed": await _seed_artifact(task_tenant, store, task_id=completed),
        "external": await _seed_artifact(task_tenant, store, task_id=waiting_external),
        "human": await _seed_artifact(task_tenant, store, task_id=waiting_human),
    }

    async with get_session_factory()() as session:
        expired = await ArtifactRetentionRepository(session).expired(
            cutoff=NOW - timedelta(days=RETENTION_DAYS),
            limit=100,
            tenant_id=task_tenant.tenant_id,
        )

    assert {row.artifact_id for row in expired} == {artifacts["completed"]}


async def test_runtime_cleanup_removes_expired_task_artifacts_only(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    store = NfsArtifactStore(artifact_root)
    execution_id = await _seed_execution(task_tenant, status="COMPLETED")
    artifact_id = await _seed_artifact(task_tenant, store, task_id=execution_id)
    run_artifact = await _seed_run_artifact(task_tenant, store)
    key = next(row["storage_key"] for row in await _artifact_rows(task_tenant) if row["id"] == artifact_id)

    report = await _retention(task_tenant, artifact_root)

    assert report.removed == 1
    assert report.failed == 0
    remaining = {row["id"]: row for row in await _artifact_rows(task_tenant)}
    assert set(remaining) == {run_artifact}
    assert not (artifact_root / str(key)).exists()
    # Run 产物不受 Task 保留清理影响。
    run_key = remaining[run_artifact]["storage_key"]
    assert (artifact_root / str(run_key)).is_file()


async def test_waiting_executions_keep_their_artifacts(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    store = NfsArtifactStore(artifact_root)
    waiting = [
        await _seed_execution(task_tenant, status="WAITING_EXTERNAL", finished=False),
        await _seed_execution(task_tenant, status="WAITING_HUMAN", finished=False),
    ]
    for execution_id in waiting:
        await _seed_artifact(task_tenant, store, task_id=execution_id)

    report = await _retention(task_tenant, artifact_root)

    assert report.removed == 0
    assert len(await _artifact_rows(task_tenant)) == 2


async def test_all_immutable_versions_are_removed_with_the_row(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    """只删当前键会把历史版本留在盘上：行没了就再没有记录指向它们。"""
    store = NfsArtifactStore(artifact_root)
    execution_id = await _seed_execution(task_tenant, status="COMPLETED")
    artifact_id = await _seed_artifact(task_tenant, store, task_id=execution_id, data=b"v1")
    current = f"tasks/{task_tenant.tenant_id}/{execution_id}/{artifact_id}/v2"
    previous = f"tasks/{task_tenant.tenant_id}/{execution_id}/{artifact_id}/v1"
    store.write(current, b"v2")
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.artifact SET storage_key = :current,"
                " metadata_json = jsonb_set(metadata_json, '{versions}',"
                " CAST(:versions AS jsonb)) WHERE id = :id"
            ),
            {
                "current": current,
                "versions": f'[{{"version": 1, "key": "{previous}", "size": 2}}]',
                "id": artifact_id,
            },
        )
        await session.commit()

    report = await _retention(task_tenant, artifact_root)

    assert report.removed == 1
    assert not (artifact_root / current).exists()
    assert not (artifact_root / previous).exists()


async def test_unlink_failure_keeps_the_row_for_retry(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    execution_id = await _seed_execution(task_tenant, status="COMPLETED")
    artifact_id = await _seed_artifact(
        task_tenant, NfsArtifactStore(artifact_root), task_id=execution_id
    )
    row = next(row for row in await _artifact_rows(task_tenant) if row["id"] == artifact_id)
    path = artifact_root / str(row["storage_key"])
    path.unlink(missing_ok=True)
    path.mkdir(parents=True)  # unlink 一个目录 ⇒ OSError：模拟删字节失败

    failed = await _retention(task_tenant, artifact_root)

    assert failed.failed == 1 and failed.removed == 0
    assert [r["id"] for r in await _artifact_rows(task_tenant)] == [artifact_id]

    path.rmdir()
    retried = await _retention(task_tenant, artifact_root)

    assert retried.removed == 1
    assert await _artifact_rows(task_tenant) == []


async def test_cleaned_artifact_is_not_downloadable_but_never_faked(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    store = NfsArtifactStore(artifact_root)
    execution_id = await _seed_execution(task_tenant, status="COMPLETED")
    artifact_id = await _seed_artifact(task_tenant, store, task_id=execution_id)
    async with get_session_factory()() as session:
        checksum = await session.scalar(
            sa.select(Artifact.checksum).where(Artifact.id == artifact_id)
        )
        # 清理前：跨用户不可见（404 语义），owner 能取到指定版本。
        with pytest.raises(AppError) as denied:
            await resolve_task_artifact_version(
                session,
                tenant_id=task_tenant.tenant_id,
                artifact_id=artifact_id,
                version="1",
                checksum=checksum or "",
                user_id=uuid.uuid4(),
                agent_id=task_tenant.agent_id,
                store=store,
            )
        assert denied.value.code == "TASK_ARTIFACT_NOT_FOUND"

    await _retention(task_tenant, artifact_root)

    async with get_session_factory()() as session:
        with pytest.raises(AppError) as missing:
            await resolve_task_artifact_version(
                session,
                tenant_id=task_tenant.tenant_id,
                artifact_id=artifact_id,
                version="1",
                checksum=checksum or "",
                user_id=task_tenant.platform_user_id,
                agent_id=task_tenant.agent_id,
                store=store,
            )
    assert missing.value.code == "TASK_ARTIFACT_NOT_FOUND"


async def test_bounded_batches_walk_every_candidate(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    store = NfsArtifactStore(artifact_root)
    execution_id = await _seed_execution(task_tenant, status="COMPLETED")
    for age in (150, 140, 130):
        await _seed_artifact(task_tenant, store, task_id=execution_id, age_days=age)

    report = await _retention(task_tenant, artifact_root, batch_size=1)

    assert report.removed == 3
    assert await _artifact_rows(task_tenant) == []


async def test_missing_artifact_root_refuses_to_delete_metadata(
    task_tenant: TenantContext, artifact_root: Path
) -> None:
    """根没挂上就不许删行：否则删掉的是元数据，字节还在（或还在别处），结果不可恢复。"""
    execution_id = await _seed_execution(task_tenant, status="COMPLETED")
    await _seed_artifact(task_tenant, NfsArtifactStore(artifact_root), task_id=execution_id)
    missing_root = artifact_root / "not-mounted"

    with pytest.raises(RuntimeError):
        await _retention(task_tenant, missing_root)

    assert len(await _artifact_rows(task_tenant)) == 1
