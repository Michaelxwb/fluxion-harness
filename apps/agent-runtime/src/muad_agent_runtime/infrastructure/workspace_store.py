"""``task.workspace`` / ``task.workspace_lease`` 的 PostgreSQL 适配器。

Runtime 在执行前 acquire（复用/创建 workspace、touch last_access、幂等续租），执行后
release（软删租约）。时间全部取 PG ``now()``：租约归属跨 Pod，不能用本机墙钟判定。

``DELETING``/``DELETED`` 的 workspace 拒绝新执行并抛 ``WorkspaceDeleting``；本模块不实现
配额/TTL/清理，那些归 Worker 的 ``workspace.maintenance`` 循环（TASK-006）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

import sqlalchemy as sa
from muad_agent_core.sandbox.workspace import (
    WORKSPACE_STATE_ACTIVE,
    WorkspaceDeleting,
    WorkspaceLeaseRecord,
    WorkspaceUnavailable,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models.workspace import Workspace, WorkspaceLease

logger = logging.getLogger("muad_agent_runtime.workspace")

SessionFactoryProvider = Callable[[], async_sessionmaker[AsyncSession]]


def _uuid(value: str | uuid.UUID, label: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise WorkspaceUnavailable(f"invalid workspace {label}: {value!r}") from exc


def _lease_until(lease_sec: float) -> sa.ColumnElement[datetime]:
    return sa.func.now() + sa.text("make_interval(secs => :lease_sec)").bindparams(
        lease_sec=lease_sec
    )


class SqlWorkspaceMetadataStore:
    """``WorkspaceMetadataStore`` 协议的 PG 实现（每次 acquire/release 独立短事务）。"""

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


__all__ = ["SessionFactoryProvider", "SqlWorkspaceMetadataStore"]
