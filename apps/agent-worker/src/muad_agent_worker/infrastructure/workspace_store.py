"""Worker 侧 ``task.workspace`` / ``task.workspace_lease`` 适配器。

Worker 执行 Skill 前需要 acquire 租约并更新 ``last_access_time``；配额检查用 PG 里的
``size_bytes`` / ``file_count``（执行后由 ``WorkspaceStore.refresh_usage`` 回写），
不在热路径上每次 ``du`` 全盘。TTL 清理的认领/删除在 ``worker/workspace_maintenance.py``。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from muad_agent_core.sandbox.workspace import (
    WORKSPACE_STATE_ACTIVE,
    WorkspaceDeleting,
    WorkspaceLeaseRecord,
    WorkspaceUnavailable,
    WorkspaceUsage,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models.workspace import Workspace, WorkspaceLease

logger = logging.getLogger("muad_agent_worker.workspace")

SessionFactoryProvider = Callable[[], async_sessionmaker[AsyncSession]]


def _uuid(value: str | uuid.UUID, label: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise WorkspaceUnavailable(f"invalid workspace {label}: {value!r}") from exc


def _lease_until(lease_sec: float) -> sa.ColumnElement[Any]:
    return sa.func.now() + sa.text("make_interval(secs => :lease_sec)").bindparams(
        lease_sec=lease_sec
    )


class WorkerWorkspaceMetadataStore:
    """``WorkspaceMetadataStore`` + ``WorkspaceUsageStore`` 的 PG 实现。"""

    def __init__(self, session_factory_provider: SessionFactoryProvider) -> None:
        self._session_factory_provider = session_factory_provider

    async def acquire(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        execution_id: str,
        run_id: str,
        task_id: str,
        lease_sec: float,
    ) -> WorkspaceLeaseRecord:
        user_uuid = _uuid(user_id, "user_id")
        agent_uuid = _uuid(agent_id, "agent_id")
        if not tenant_id:
            raise WorkspaceUnavailable("workspace tenant_id must not be empty")
        if not execution_id:
            raise WorkspaceUnavailable("workspace execution_id must not be empty")
        if lease_sec <= 0:
            raise WorkspaceUnavailable("workspace lease_sec must be positive")
        factory = self._session_factory_provider()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(Workspace)
                    .values(
                        tenant_id=tenant_id,
                        user_id=user_uuid,
                        agent_id=agent_uuid,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            Workspace.tenant_id,
                            Workspace.user_id,
                            Workspace.agent_id,
                        ],
                        index_where=sa.text("is_deleted = false"),
                    )
                )
                workspace = await session.scalar(
                    sa.select(Workspace)
                    .where(
                        Workspace.tenant_id == tenant_id,
                        Workspace.user_id == user_uuid,
                        Workspace.agent_id == agent_uuid,
                        Workspace.is_deleted.is_(False),
                    )
                    .with_for_update()
                )
                if workspace is None:
                    raise WorkspaceUnavailable(
                        "workspace metadata is unavailable after upsert"
                    )
                if workspace.state != WORKSPACE_STATE_ACTIVE:
                    raise WorkspaceDeleting(
                        f"workspace is not active: {workspace.state}"
                    )
                workspace.last_access_time = sa.func.now()
                workspace.update_time = sa.func.now()
                lease_until = _lease_until(lease_sec)
                await session.execute(
                    pg_insert(WorkspaceLease)
                    .values(
                        workspace_id=workspace.id,
                        run_id=run_id,
                        task_id=task_id,
                        execution_id=execution_id,
                        lease_until=lease_until,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            WorkspaceLease.workspace_id,
                            WorkspaceLease.execution_id,
                        ],
                        index_where=sa.text("is_deleted = false"),
                        set_={
                            "run_id": run_id,
                            "task_id": task_id,
                            "lease_until": _lease_until(lease_sec),
                            "update_time": sa.func.now(),
                        },
                    )
                )
                lease = await session.scalar(
                    sa.select(WorkspaceLease).where(
                        WorkspaceLease.workspace_id == workspace.id,
                        WorkspaceLease.execution_id == execution_id,
                        WorkspaceLease.is_deleted.is_(False),
                    )
                )
                if lease is None:
                    raise WorkspaceUnavailable(
                        "workspace lease is unavailable after upsert"
                    )
                return WorkspaceLeaseRecord(
                    id=lease.id,
                    workspace_id=workspace.id,
                    execution_id=lease.execution_id,
                    lease_until=lease.lease_until,
                )

    async def release(self, lease_id: uuid.UUID) -> None:
        factory = self._session_factory_provider()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    sa.update(WorkspaceLease)
                    .where(
                        WorkspaceLease.id == lease_id,
                        WorkspaceLease.is_deleted.is_(False),
                    )
                    .values(is_deleted=True, update_time=sa.func.now())
                )

    async def workspace_usage(self, workspace_id: uuid.UUID) -> WorkspaceUsage:
        factory = self._session_factory_provider()
        async with factory() as session:
            row = (
                await session.execute(
                    sa.select(Workspace.size_bytes, Workspace.file_count).where(
                        Workspace.id == workspace_id,
                        Workspace.is_deleted.is_(False),
                    )
                )
            ).one_or_none()
        if row is None:
            raise WorkspaceUnavailable(f"workspace metadata is unavailable: {workspace_id}")
        return WorkspaceUsage(size_bytes=int(row.size_bytes), file_count=int(row.file_count))

    async def user_usage(self, tenant_id: str, user_id: str) -> WorkspaceUsage:
        user_uuid = _uuid(user_id, "user_id")
        factory = self._session_factory_provider()
        async with factory() as session:
            row = (
                await session.execute(
                    sa.select(
                        sa.func.coalesce(sa.func.sum(Workspace.size_bytes), 0),
                        sa.func.coalesce(sa.func.sum(Workspace.file_count), 0),
                    ).where(
                        Workspace.tenant_id == tenant_id,
                        Workspace.user_id == user_uuid,
                        Workspace.is_deleted.is_(False),
                    )
                )
            ).one()
        return WorkspaceUsage(size_bytes=int(row[0]), file_count=int(row[1]))

    async def record_usage(
        self, workspace_id: uuid.UUID, usage: WorkspaceUsage
    ) -> None:
        factory = self._session_factory_provider()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    sa.update(Workspace)
                    .where(
                        Workspace.id == workspace_id,
                        Workspace.is_deleted.is_(False),
                    )
                    .values(
                        size_bytes=usage.size_bytes,
                        file_count=usage.file_count,
                        update_time=sa.func.now(),
                    )
                )


__all__ = ["SessionFactoryProvider", "WorkerWorkspaceMetadataStore"]
