"""定时任务历史的保留清理（design §3.5.11「保留」、§3.3.6/§3.3.7；TASK-031）。

**清理不是请求**：本模块只被独立维护循环调用（`ScheduleRetentionLoop`），不在任何用户 GET 里
扫描或删除；每轮先在事务外取一次平台设置（网络），再有界地删一批（事务）。

**两个保留期，两个流**（唯一权威是平台设置 `schedule.max_fire_history_days` /
`schedule.max_execution_history_days`，这里不写死天数）：

- **Execution 流**：终态（`COMPLETED/FAILED/CANCELLED`）且 `COALESCE(finished_at, create_time)`
  早于执行保留边界的执行，连同它的 checkpoint、Human 交互、投递（含尝试）、终态 outbox 与
  审计事件一起清掉。Snapshot 内联在 Execution 行上（§3.5.7），因此随执行一起释放——**不在执行
  完成时立即删**。
- **Fire 流**：`create_time` 早于 Fire 保留边界的 Fire 行连同 FIRE 事件。

**保护的是"还在用"，不是"看起来旧"**（几条都是硬事实，删错就是丢恢复材料/丢重投）：

- **三类等待态**（`QUEUED/RUNNING/WAITING_EXTERNAL/WAITING_HUMAN`）的执行**永不在候选里**：
  它们还要恢复、续接、重投，产物与 checkpoint 都是恢复材料。只认终态是这里唯一的安全谓词。
- **还有子执行**的父执行不删（父子是 NO ACTION 自引用 FK）；子执行先过期、下一轮父执行才可删。
- **仍有 PENDING 投递**或 **PENDING 结果 outbox** 的执行不删：它们是"终态但还没送出去"，
  删掉等于把待重投的结果静默丢掉。
- **被执行引用的 Fire 不删**（`task_execution.schedule_fire_id` 是 NO ACTION FK）；先删执行时
  把 `schedule_fire.execution_id` 摘掉，Fire 行本身留给 Fire 保留期——Fire 历史不会因为执行
  被清而消失。

**引用不删只摘**：`task_submission`（受理记录）与 `runtime_operation`（准入/取消身份）保留，
只把指向被执行行的 FK 置 NULL——它们是审计与幂等身份，不属于可回收的"历史载荷"。

**有界前进**：每个流各自一条 `(create_time, id)` 前进游标；一批最多 `batch_size` 行，游标停在
**本批最后一条被检视的行**（不是最后一条被删的行）——被保护的行因此不会把后面的行永远挡住。
一轮最多 `max_batches` 批，批与批之间各自提交，锁不会跨批持有。

**租户隔离**：`tenant_id` 是必填谓词（不是可选过滤器）。这不是给测试留的口子——一条没有租户
谓词的破坏性清理在共享库上没法安全执行；运维口径同样按租户清、按租户对账。

**失败可见**：单批失败由循环记 `logger.exception` 后继续；不静默吞掉（§3.5.11 的可对账要求）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from muad_contracts import TaskStatus
from sqlalchemy import Row
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime_operations import RuntimeOperation, RuntimeResultOutbox
from ..infrastructure.models.task import (
    ScheduleFire,
    TaskDelivery,
    TaskEvent,
    TaskExecution,
    TaskExecutionCheckpoint,
    TaskHumanInteraction,
)
from ..infrastructure.models.task_submission import TaskSubmission
from ..metrics import record_loop_tick
from .platform_settings import resolve_platform_settings
from .ports import PlatformSettingsClient
from .task_events import EntityType

logger = logging.getLogger("muad_agent_worker.application.schedule_retention")

#: 单批候选上界：清理是破坏性维护动作，有界前进才可中断、可对账。
RETENTION_BATCH_SIZE = 200
#: 一轮最多推进几批：单轮耗时与锁持有时间都要有上界，剩余部分留给下一轮。
RETENTION_MAX_BATCHES_PER_TICK = 5

#: 可回收的执行状态：只有这些才可能失去恢复/重投价值。
TERMINAL_TASK_STATUSES: tuple[str, ...] = (
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
)
#: 仍需要投递/发件的状态：命中即整条执行保留（终态但没送出去的不能删）。
PENDING_DELIVERY_STATUS = "PENDING"
PENDING_OUTBOX_STATUS = "PENDING"

#: 位置游标：`(create_time, id)` 的字典序；两个流各持一条。
Position = tuple[datetime, uuid.UUID]


@dataclass(frozen=True, slots=True)
class RetentionCursor:
    """两个清理流各自的下一批起点；`None` 表示该流已扫到末尾（下一轮从头发起）。"""

    execution: Position | None = None
    fire: Position | None = None


@dataclass(frozen=True, slots=True)
class RetentionBatchReport:
    """一批清理的可对账结果（条数 + 前进游标）。"""

    executions: int = 0
    fires: int = 0
    checkpoints: int = 0
    human_interactions: int = 0
    deliveries: int = 0
    delivery_attempts: int = 0
    events: int = 0
    outbox_rows: int = 0
    submissions_detached: int = 0
    operations_detached: int = 0
    inspected_executions: int = 0
    inspected_fires: int = 0
    cursor: RetentionCursor | None = None

    def summary(self) -> str:
        return (
            f"schedule-retention: executions={self.executions} fires={self.fires} "
            f"checkpoints={self.checkpoints} human={self.human_interactions} "
            f"deliveries={self.deliveries} attempts={self.delivery_attempts} "
            f"events={self.events} outbox={self.outbox_rows} "
            f"submissions_detached={self.submissions_detached} "
            f"operations_detached={self.operations_detached} "
            f"inspected=({self.inspected_executions},{self.inspected_fires})"
        )


def _pending_delivery() -> sa.ColumnElement[bool]:
    return (
        sa.select(sa.literal(1))
        .select_from(TaskDelivery)
        .where(
            TaskDelivery.execution_id == TaskExecution.id,
            TaskDelivery.is_deleted.is_(False),
            TaskDelivery.status == PENDING_DELIVERY_STATUS,
        )
        .exists()
    )


def _pending_outbox() -> sa.ColumnElement[bool]:
    return (
        sa.select(sa.literal(1))
        .select_from(RuntimeResultOutbox)
        .where(
            RuntimeResultOutbox.task_id == TaskExecution.id,
            RuntimeResultOutbox.is_deleted.is_(False),
            RuntimeResultOutbox.status == PENDING_OUTBOX_STATUS,
        )
        .exists()
    )


def _execution_candidate_query(
    *, tenant_id: str, cutoff: datetime, cursor: Position | None, limit: int
) -> sa.Select[tuple[uuid.UUID, datetime]]:
    child = aliased(TaskExecution)
    has_child = (
        sa.select(sa.literal(1))
        .select_from(child)
        .where(child.parent_id == TaskExecution.id, child.is_deleted.is_(False))
        .exists()
    )
    query = (
        sa.select(TaskExecution.id, TaskExecution.create_time)
        .where(
            TaskExecution.tenant_id == tenant_id,
            TaskExecution.is_deleted.is_(False),
            TaskExecution.status.in_(TERMINAL_TASK_STATUSES),
            sa.func.coalesce(TaskExecution.finished_at, TaskExecution.create_time) < cutoff,
            sa.not_(has_child),
            sa.not_(_pending_delivery()),
            sa.not_(_pending_outbox()),
        )
        .order_by(TaskExecution.create_time, TaskExecution.id)
        .limit(limit)
    )
    if cursor is not None:
        query = query.where(sa.tuple_(TaskExecution.create_time, TaskExecution.id) > cursor)
    return query


def _fire_candidate_query(
    *, tenant_id: str, cutoff: datetime, cursor: Position | None, limit: int
) -> sa.Select[tuple[uuid.UUID, datetime]]:
    execution = aliased(TaskExecution)
    still_referenced = (
        sa.select(sa.literal(1))
        .select_from(execution)
        .where(execution.schedule_fire_id == ScheduleFire.id, execution.is_deleted.is_(False))
        .exists()
    )
    query = (
        sa.select(ScheduleFire.id, ScheduleFire.create_time)
        .where(
            ScheduleFire.tenant_id == tenant_id,
            ScheduleFire.is_deleted.is_(False),
            ScheduleFire.create_time < cutoff,
            sa.not_(still_referenced),
        )
        .order_by(ScheduleFire.create_time, ScheduleFire.id)
        .limit(limit)
    )
    if cursor is not None:
        query = query.where(sa.tuple_(ScheduleFire.create_time, ScheduleFire.id) > cursor)
    return query


def _next_cursor(rows: Sequence[Row[tuple[uuid.UUID, datetime]]]) -> Position:
    """游标停在**本批最后一条被检视的行**：被保护的行不会把后面的候选永久挡住。"""
    identifier, created = rows[-1]
    return (created, identifier)


@dataclass(frozen=True, slots=True)
class _StreamCounts:
    """单个清理流的本批计数（两条流字段不同，报告时再各自取用）。"""

    executions: int = 0
    fires: int = 0
    checkpoints: int = 0
    human_interactions: int = 0
    deliveries: int = 0
    delivery_attempts: int = 0
    events: int = 0
    outbox_rows: int = 0
    submissions_detached: int = 0
    operations_detached: int = 0
    inspected: int = 0


class ScheduleRetentionService:
    """一批有界的任务历史清理；**不提交**，事务边界由调用方持有。"""

    def __init__(self, session: AsyncSession, *, batch_size: int = RETENTION_BATCH_SIZE) -> None:
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
            raise ValueError("invalid schedule retention batch size")
        self._session = session
        self._batch_size = batch_size

    async def run_batch(
        self,
        *,
        tenant_id: str,
        fire_history_days: int,
        execution_history_days: int,
        cursor: RetentionCursor | None = None,
        now: datetime | None = None,
    ) -> RetentionBatchReport:
        """删一批；返回本批的可对账计数与**前进后的**游标（`None` = 两个流都已扫完）。"""
        moment = now if now is not None else await database_now(self._session)
        cursor = cursor or RetentionCursor()
        executions, execution_cursor = await self._clean_executions(
            tenant_id=tenant_id,
            cutoff=moment - timedelta(days=execution_history_days),
            cursor=cursor.execution,
        )
        fires, fire_cursor = await self._clean_fires(
            tenant_id=tenant_id,
            cutoff=moment - timedelta(days=fire_history_days),
            cursor=cursor.fire,
        )
        return RetentionBatchReport(
            executions=executions.executions,
            fires=fires.fires,
            checkpoints=executions.checkpoints,
            human_interactions=executions.human_interactions,
            deliveries=executions.deliveries,
            delivery_attempts=executions.delivery_attempts,
            events=executions.events + fires.events,
            outbox_rows=executions.outbox_rows,
            submissions_detached=executions.submissions_detached,
            operations_detached=executions.operations_detached,
            inspected_executions=executions.inspected,
            inspected_fires=fires.inspected,
            cursor=None
            if execution_cursor is None and fire_cursor is None
            else RetentionCursor(execution=execution_cursor, fire=fire_cursor),
        )

    async def _clean_executions(
        self, *, tenant_id: str, cutoff: datetime, cursor: Position | None
    ) -> tuple[_StreamCounts, Position | None]:
        rows = (
            await self._session.execute(
                _execution_candidate_query(
                    tenant_id=tenant_id, cutoff=cutoff, cursor=cursor, limit=self._batch_size
                )
            )
        ).all()
        if not rows:
            return _StreamCounts(), None
        ids = [row[0] for row in rows]
        counts = await self._delete_execution_rows(tenant_id=tenant_id, execution_ids=ids)
        return (
            replace(counts, inspected=len(rows)),
            _next_cursor(rows) if len(rows) == self._batch_size else None,
        )

    async def _delete_execution_rows(
        self, *, tenant_id: str, execution_ids: list[uuid.UUID]
    ) -> _StreamCounts:
        """按 FK 顺序清子项与执行行；引用方只摘 FK 不删行（见模块说明）。"""
        params = {"ids": execution_ids}
        #: outbox 同时引用执行与事件：必须排在事件删除之前。
        outbox = await self._execute(
            sa.delete(RuntimeResultOutbox).where(RuntimeResultOutbox.task_id.in_(execution_ids))
        )
        submissions = await self._execute(
            sa.update(TaskSubmission)
            .where(TaskSubmission.task_id.in_(execution_ids))
            .values(task_id=None)
        )
        operations = await self._execute(
            sa.update(RuntimeOperation)
            .where(
                sa.or_(
                    RuntimeOperation.task_execution_id.in_(execution_ids),
                    RuntimeOperation.task_id.in_(execution_ids),
                )
            )
            .values(task_execution_id=None, task_id=None)
        )
        attempts = await self._execute(
            sa.text(
                "DELETE FROM task.task_delivery_attempt WHERE delivery_id IN "
                "(SELECT id FROM task.task_delivery WHERE execution_id IN :ids)"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            params,
        )
        events = await self._execute(
            sa.text(
                "DELETE FROM task.task_event WHERE tenant_id = :tenant_id AND ("
                " (entity_type = :execution AND entity_id IN :ids)"
                " OR (entity_type = :human AND entity_id IN "
                "     (SELECT id FROM task.task_human_interaction WHERE execution_id IN :ids))"
                " OR (entity_type = :delivery AND entity_id IN "
                "     (SELECT id FROM task.task_delivery WHERE execution_id IN :ids)))"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {
                "tenant_id": tenant_id,
                "execution": EntityType.EXECUTION.value,
                "human": EntityType.HUMAN.value,
                "delivery": EntityType.DELIVERY.value,
                **params,
            },
        )
        deliveries = await self._execute(
            sa.delete(TaskDelivery).where(TaskDelivery.execution_id.in_(execution_ids))
        )
        humans = await self._execute(
            sa.delete(TaskHumanInteraction).where(
                TaskHumanInteraction.execution_id.in_(execution_ids)
            )
        )
        checkpoints = await self._execute(
            sa.delete(TaskExecutionCheckpoint).where(
                TaskExecutionCheckpoint.execution_id.in_(execution_ids)
            )
        )
        #: Fire 是历史事实，不随执行一起消失：只摘掉指向被执行行的引用。
        await self._execute(
            sa.update(ScheduleFire)
            .where(ScheduleFire.execution_id.in_(execution_ids))
            .values(execution_id=None)
        )
        executions = await self._execute(
            sa.delete(TaskExecution).where(TaskExecution.id.in_(execution_ids))
        )
        return _StreamCounts(
            executions=executions,
            checkpoints=checkpoints,
            human_interactions=humans,
            deliveries=deliveries,
            delivery_attempts=attempts,
            events=events,
            outbox_rows=outbox,
            submissions_detached=submissions,
            operations_detached=operations,
        )

    async def _clean_fires(
        self, *, tenant_id: str, cutoff: datetime, cursor: Position | None
    ) -> tuple[_StreamCounts, Position | None]:
        rows = (
            await self._session.execute(
                _fire_candidate_query(
                    tenant_id=tenant_id, cutoff=cutoff, cursor=cursor, limit=self._batch_size
                )
            )
        ).all()
        if not rows:
            return _StreamCounts(), None
        ids = [row[0] for row in rows]
        events = await self._execute(
            sa.delete(TaskEvent).where(
                TaskEvent.tenant_id == tenant_id,
                TaskEvent.entity_type == EntityType.FIRE.value,
                TaskEvent.entity_id.in_(ids),
            )
        )
        removed = await self._execute(sa.delete(ScheduleFire).where(ScheduleFire.id.in_(ids)))
        return (
            _StreamCounts(fires=removed, events=events, inspected=len(rows)),
            _next_cursor(rows) if len(rows) == self._batch_size else None,
        )

    async def _execute(
        self, statement: sa.Executable, params: Mapping[str, object] | None = None
    ) -> int:
        result = await self._session.execute(statement, dict(params or {}))
        return int(cast(CursorResult[Any], result).rowcount or 0)


@dataclass(frozen=True, slots=True)
class RetentionTick:
    """一轮维护的可对账结果（多批合计）。"""

    executions: int = 0
    fires: int = 0
    events: int = 0
    batches: int = 0
    settings_revision: int = 0


class ScheduleRetentionLoop:
    """独立维护节拍：事务外取设置 → 有界批 → 提交；单轮失败留痕后继续。

    租户取本部署租户（与 Runtime 的保留循环同口径）：不拿一个租户的设置去删别的租户的行。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings_client: PlatformSettingsClient,
        *,
        tenant_id: str,
        interval_sec: float,
        batch_size: int = RETENTION_BATCH_SIZE,
        max_batches: int = RETENTION_MAX_BATCHES_PER_TICK,
    ) -> None:
        if interval_sec <= 0:
            raise ValueError("schedule retention interval must be positive")
        if isinstance(max_batches, bool) or max_batches < 1:
            raise ValueError("invalid schedule retention batch count")
        self._session_factory = session_factory
        self._settings_client = settings_client
        self._tenant_id = tenant_id
        self._interval_sec = interval_sec
        self._batch_size = batch_size
        self._max_batches = max_batches

    async def run_once(self, *, now: datetime | None = None) -> RetentionTick:
        snapshot = await self._settings_client.fetch_snapshot(tenant_id=self._tenant_id)
        settings = resolve_platform_settings(snapshot).schedule
        cursor: RetentionCursor | None = RetentionCursor()
        executions = fires = events = batches = 0
        while cursor is not None and batches < self._max_batches:
            async with self._session_factory() as session, session.begin():
                report = await ScheduleRetentionService(
                    session, batch_size=self._batch_size
                ).run_batch(
                    tenant_id=self._tenant_id,
                    fire_history_days=settings.max_fire_history_days,
                    execution_history_days=settings.max_execution_history_days,
                    cursor=cursor,
                    now=now,
                )
            executions += report.executions
            fires += report.fires
            events += report.events
            batches += 1
            cursor = report.cursor
            if report.executions or report.fires:
                logger.info("schedule_retention_batch", extra={"summary": report.summary()})
        return RetentionTick(
            executions=executions,
            fires=fires,
            events=events,
            batches=batches,
            settings_revision=snapshot.revision,
        )

    async def run_forever(self) -> None:
        """独立节拍；单次失败记日志后继续，不杀死循环。"""
        while True:
            try:
                tick = await self.run_once()
                if tick.executions or tick.fires:
                    logger.info(
                        "schedule_retention_tick",
                        extra={
                            "executions": tick.executions,
                            "fires": tick.fires,
                            "events": tick.events,
                            "batches": tick.batches,
                            "settings_revision": tick.settings_revision,
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 维护循环必须继续，失败要留痕
                logger.exception("schedule_retention_failed")
                record_loop_tick("worker.schedule_retention", "failed")
            else:
                record_loop_tick("worker.schedule_retention", "ok")
            await asyncio.sleep(self._interval_sec)


__all__ = [
    "RETENTION_BATCH_SIZE",
    "RETENTION_MAX_BATCHES_PER_TICK",
    "RetentionBatchReport",
    "RetentionCursor",
    "RetentionTick",
    "ScheduleRetentionLoop",
    "ScheduleRetentionService",
]
