"""Worker 内置 ``workspace.maintenance``：TTL 认领、删除、失败重试。

多副本安全：

- 认领在 PG 事务里用 ``FOR UPDATE SKIP LOCKED``，同一行只会被一个副本拿到；
- 删除期间持有**会话级** advisory lock；另一个副本即使看到 ``DELETING`` 行也无法认领；
- 删除失败保留 ``DELETING`` + ``cleanup_error``，释放锁，下一轮（或另一副本）重试；
- 删除成功置 ``DELETED`` 且 ``is_deleted=true``，释放唯一键供新 workspace 创建。

时间权威是 PG ``now()``（测试可注入 timezone-aware 的 ``now`` 用于边界验证）。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa
from muad_agent_core.sandbox.workspace import WorkspaceLayout
from muad_common.workspaces import DRAFT_ACTIVITY_MARKER
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models.workspace import Workspace, WorkspaceLease
from ..metrics import record_workspace_cleanup

logger = logging.getLogger("muad_agent_worker.worker.workspace_maintenance")

WORKSPACE_ACTIVE = "ACTIVE"
WORKSPACE_DELETING = "DELETING"
WORKSPACE_DELETED = "DELETED"
DEFAULT_BATCH_SIZE = 20


@dataclass(frozen=True, slots=True)
class ClaimedWorkspace:
    id: uuid.UUID
    tenant_id: str
    user_id: uuid.UUID
    agent_id: uuid.UUID
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MaintenanceSummary:
    scanned: int = 0
    claimed: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    released_bytes: int = 0


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    claimed: tuple[ClaimedWorkspace, ...]
    skipped: int


def _advisory_key(workspace_id: uuid.UUID) -> sa.ColumnElement[Any]:
    return sa.func.hashtextextended(sa.cast(str(workspace_id), sa.Text), 0)


def _active_lease(reference: datetime) -> sa.ColumnElement[bool]:
    return sa.exists().where(
        WorkspaceLease.workspace_id == Workspace.id,
        WorkspaceLease.is_deleted.is_(False),
        WorkspaceLease.lease_until > reference,
    )


def _due_predicate(cutoff: datetime, reference: datetime) -> sa.ColumnElement[bool]:
    active = _active_lease(reference)
    return sa.or_(
        sa.and_(
            Workspace.state == WORKSPACE_ACTIVE,
            Workspace.last_access_time <= cutoff,
            ~active,
        ),
        sa.and_(Workspace.state == WORKSPACE_DELETING, ~active),
    )


class WorkspaceMaintenanceLoop:
    """按 TTL 清理 workspace；不依赖 CronJob/管理员 Schedule。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        layout: WorkspaceLayout,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        interval_sec: float = 30.0,
        remover: Callable[[Path], None] = shutil.rmtree,
    ) -> None:
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
            raise ValueError("invalid workspace cleanup batch size")
        if interval_sec <= 0:
            raise ValueError("workspace maintenance interval must be positive")
        self._session_factory = session_factory
        self._layout = layout
        self._batch_size = batch_size
        self._interval_sec = interval_sec
        self._remover = remover

    async def run_once(self, *, now: datetime | None = None) -> MaintenanceSummary:
        """一轮清理：认领 → 删目录 → 置终态/失败；返回可测试的显式计数。"""
        async with AsyncExitStack() as stack:
            session = await stack.enter_async_context(self._session_factory())
            locks = await stack.enter_async_context(self._session_factory())
            # 事务级 advisory lock 固定在同一条连接上：整个清理循环期间有效，事务结束自动释放。
            # 不用会话级 pg_advisory_lock + 跨事务 unlock —— 连接归还池后 unlock 可能落到
            # 另一条连接上而静默失败，锁永久泄漏、DELETING 行再也不会被重试。
            await stack.enter_async_context(locks.begin())
            async with session.begin():
                reference = await self._reference_time(session, now)
                outcome = await self._claim(session, reference, locks)
            scanned = len(outcome.claimed) + outcome.skipped
            if scanned:
                record_workspace_cleanup("SCANNED", amount=scanned)
            if outcome.skipped:
                record_workspace_cleanup("SKIPPED", amount=outcome.skipped)
            deleted = failed = released_bytes = 0
            for item in outcome.claimed:
                try:
                    await self._remove(item)
                except OSError as exc:
                    failed += 1
                    await self._mark_failed(session, item, exc)
                    record_workspace_cleanup("FAILED")
                    logger.error(
                        "workspace_cleanup_failed",
                        extra={
                            "workspace_id": str(item.id),
                            "tenant_id": item.tenant_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                else:
                    if await self._mark_deleted(session, item.id):
                        deleted += 1
                        released_bytes += item.size_bytes
                        # 先记指标再 unlock/进入下一次 await：循环被取消时也不能丢删除计数。
                        record_workspace_cleanup("DELETED")
                        logger.info(
                            "workspace_cleanup",
                            extra={
                                "workspace_id": str(item.id),
                                "tenant_id": item.tenant_id,
                                "status": "DELETED",
                                "released_bytes": item.size_bytes,
                            },
                        )
                    else:
                        failed += 1
                        record_workspace_cleanup("FAILED")

            return MaintenanceSummary(
                scanned=scanned,
                claimed=len(outcome.claimed),
                deleted=deleted,
                skipped=outcome.skipped,
                failed=failed,
                released_bytes=released_bytes,
            )

    async def run_forever(self) -> None:
        """独立维护节拍；单次失败记日志/指标后继续，不杀死循环。"""
        while True:
            try:
                summary = await self.run_once()
                if summary.scanned:
                    logger.info(
                        "workspace_maintenance_tick",
                        extra={
                            "scanned": summary.scanned,
                            "deleted": summary.deleted,
                            "skipped": summary.skipped,
                            "failed": summary.failed,
                            "released_bytes": summary.released_bytes,
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 维护循环必须继续，失败要可见
                logger.exception("workspace_maintenance_failed")
                record_workspace_cleanup("FAILED")
            await asyncio.sleep(self._interval_sec)

    async def _reference_time(
        self, session: AsyncSession, now: datetime | None
    ) -> datetime:
        reference = now if now is not None else await session.scalar(select(sa.func.now()))
        assert reference is not None
        if reference.tzinfo is None:
            raise ValueError("workspace maintenance reference time must be timezone-aware")
        return reference

    async def _claim(
        self, session: AsyncSession, reference: datetime, locks: AsyncSession
    ) -> ClaimOutcome:
        cutoff = reference - timedelta(seconds=self._layout.data_ttl_seconds)
        skipped = int(
            await session.scalar(
                select(sa.func.count())
                .select_from(Workspace)
                .where(
                    Workspace.is_deleted.is_(False),
                    Workspace.state == WORKSPACE_ACTIVE,
                    Workspace.last_access_time <= cutoff,
                    _active_lease(reference),
                )
            )
            or 0
        )
        rows = (
            await session.scalars(
                select(Workspace)
                .where(
                    Workspace.is_deleted.is_(False),
                    _due_predicate(cutoff, reference),
                )
                .order_by(Workspace.last_access_time.asc())
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claimed: list[ClaimedWorkspace] = []
        for row in rows:
            if self._draft_recently_active(row, cutoff):
                continue
            if not await self._try_lock(locks, row.id):
                continue
            row.state = WORKSPACE_DELETING
            row.update_time = reference
            claimed.append(
                ClaimedWorkspace(
                    id=row.id,
                    tenant_id=row.tenant_id,
                    user_id=row.user_id,
                    agent_id=row.agent_id,
                    size_bytes=int(row.size_bytes),
                )
            )
        return ClaimOutcome(claimed=tuple(claimed), skipped=skipped)

    def _draft_recently_active(self, row: Workspace, cutoff: datetime) -> bool:
        """草稿活动标记比清理截止时间新：保留整个 agent 目录（含草稿）。"""
        directory = self._layout.workspace_dir(
            tenant_id=row.tenant_id,
            user_id=str(row.user_id),
            agent_id=str(row.agent_id),
        )
        try:
            return (directory / DRAFT_ACTIVITY_MARKER).stat().st_mtime > cutoff.timestamp()
        except OSError:
            return False

    async def _try_lock(self, locks: AsyncSession, workspace_id: uuid.UUID) -> bool:
        """在锁连接的事务里取事务级 advisory lock（事务结束自动释放）。"""
        return bool(
            await locks.scalar(
                select(sa.func.pg_try_advisory_xact_lock(_advisory_key(workspace_id)))
            )
        )

    async def _remove(self, item: ClaimedWorkspace) -> None:
        directory = self._layout.workspace_dir(
            tenant_id=item.tenant_id,
            user_id=str(item.user_id),
            agent_id=str(item.agent_id),
        )
        if directory == self._layout.root or self._layout.root not in directory.parents:
            raise OSError("refusing to remove a path outside the workspace root")
        if not directory.exists():
            return
        await asyncio.to_thread(self._remover, directory)

    async def _mark_deleted(self, session: AsyncSession, workspace_id: uuid.UUID) -> bool:
        async with session.begin():
            result = await session.execute(
                update(Workspace)
                .where(
                    Workspace.id == workspace_id,
                    Workspace.is_deleted.is_(False),
                )
                .values(
                    state=WORKSPACE_DELETED,
                    is_deleted=True,
                    cleanup_error=None,
                    size_bytes=0,
                    file_count=0,
                    update_time=sa.func.now(),
                )
            )
        return bool(cast(CursorResult[Any], result).rowcount)

    async def _mark_failed(
        self, session: AsyncSession, item: ClaimedWorkspace, error: OSError
    ) -> None:
        try:
            async with session.begin():
                await session.execute(
                    update(Workspace)
                    .where(
                        Workspace.id == item.id,
                        Workspace.is_deleted.is_(False),
                    )
                    .values(
                        state=WORKSPACE_DELETING,
                        cleanup_error=str(error)[:2000],
                        update_time=sa.func.now(),
                    )
                )
        except Exception:  # noqa: BLE001 - 原失败已记录；状态回写失败也必须可见
            logger.exception(
                "workspace_cleanup_failure_state_not_persisted",
                extra={"workspace_id": str(item.id)},
            )


__all__ = [
    "ClaimedWorkspace",
    "DEFAULT_BATCH_SIZE",
    "MaintenanceSummary",
    "WorkspaceMaintenanceLoop",
]
