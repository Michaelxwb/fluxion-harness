"""ASYNC 结果的周期补扫：独立受监督、有界且跨租户公平（design §3.1.5 第 4 条）。

为什么必须有它：结果只以 Task-owned 收件箱为准，Runtime **没有**到 Worker 的入站回调；
唤醒 hint 只是延迟优化。于是"结果到了"必须由 Worker 自己按 PG 扫描发现——否则 ready-check
之后才到达的结果（以及丢掉的 hint、Redis 不可用、进程重启）都不会让执行前进。

四条口径：

- **只推进水位，不写等待状态**：`WAITING_EXTERNAL` 的唯一写入方仍是 `WorkerLoop._wait`。
  补扫只在**已经等待**的行上推进 `wait_payload_json.operations[]` 的
  `status`/`receipt_seq`/`consumed_event_id`；水位全终态后由 claim 按 `wait_kind` 分发续接
  （外部续接不消耗业务重试，也不新建 Fire/Run）。
- **有界**：每拍最多处理 `batch_size` 行，每行最多一次 HTTP 读取。
- **跨租户公平**：按 `update_time` 升序取候选，处理过的行把 `update_time` 推到当前时刻，
  于是它排到队尾——任何租户的积压都不会把别的租户饿死。
- **迟到只记 late**：候选行在锁内已不再等待（取消/终态/已被领取）时不写状态、不回到
  QUEUED；只对已就绪结果记一条 `ASYNC/RESULT_RECEIVED{late}`。

单行失败（读不到、载荷坏）记日志后继续：一次坏行不能杀死循环，但必须看得见。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime

from muad_contracts import TaskStatus, TaskToolOperationView, WaitKind
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.agentic.async_results import (
    OperationReader,
    advance_watermarks,
    all_settled,
    payload_from_watermarks,
    read_operations,
    watermarks_from_payload,
)
from ..application.task_events import (
    EntityType,
    TaskEventSeed,
    TaskEventType,
    append_events,
)
from ..application.task_service import TERMINAL_STATUSES
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.task import TaskExecution
from ..infrastructure.task_operation_client import TaskOperationError
from ..metrics import record_loop_tick

logger = logging.getLogger("muad_agent_worker.worker.async_sweep")


class AsyncResultSweeper:
    """把 Task-owned 收件箱里的就绪结果推进到等待水位上。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        operations: OperationReader,
        *,
        interval_sec: float,
        batch_size: int = 32,
    ) -> None:
        if isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("invalid async result sweep batch size")
        if interval_sec <= 0:
            raise ValueError("invalid async result sweep interval")
        self._session_factory = session_factory
        self._operations = operations
        self._interval_sec = float(interval_sec)
        self._batch_size = batch_size

    async def run_forever(self) -> None:
        """独立节拍：单次失败记日志与指标后继续，不杀死循环（同维护循环口径）。"""
        while True:
            try:
                await self.sweep()
            except Exception:
                logger.exception("async_result_sweep_tick_failed")
                record_loop_tick("worker.async_sweep", "failed")
            else:
                record_loop_tick("worker.async_sweep", "ok")
            await asyncio.sleep(self._interval_sec)

    async def sweep(self, *, now: datetime | None = None) -> int:
        """扫一批等待中的执行；返回推进了水位的行数。"""
        async with self._session_factory() as session:
            moment = now if now is not None else await database_now(session)
            candidates = list(
                await session.scalars(
                    select(TaskExecution)
                    .where(
                        TaskExecution.status == str(TaskStatus.WAITING_EXTERNAL),
                        TaskExecution.wait_kind == WaitKind.ASYNC_OPERATION.value,
                        TaskExecution.is_deleted.is_(False),
                    )
                    .order_by(TaskExecution.update_time, TaskExecution.id)
                    .limit(self._batch_size)
                )
            )
        advanced = 0
        for task in candidates:
            try:
                advanced += await self._sweep_one(task, moment)
            except Exception:
                logger.warning(
                    "async_result_sweep_row_failed",
                    extra={"execution_id": str(task.id), "tenant_id": task.tenant_id},
                    exc_info=True,
                )
        if advanced:
            logger.info("async_result_sweep_advanced", extra={"executions": advanced})
        return advanced

    async def _sweep_one(self, task: TaskExecution, moment: datetime) -> int:
        watermarks = watermarks_from_payload(task.wait_payload_json)
        if not watermarks:
            await self._rotate(task, moment)
            return 0
        try:
            views = await read_operations(
                self._operations,
                tenant_id=task.tenant_id,
                actor_user_id=task.actor_user_id,
                execution_id=task.id,
            )
        except TaskOperationError as exc:
            logger.warning(
                "async_result_sweep_read_failed",
                extra={
                    "execution_id": str(task.id),
                    "tenant_id": task.tenant_id,
                    "code": exc.code,
                },
            )
            # 读不到也要轮转：否则 Runtime 抖动期间队头永远占位，后面的行（别的租户）被饿死。
            await self._rotate(task, moment)
            return 0
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(TaskExecution)
                .where(
                    TaskExecution.id == task.id,
                    TaskExecution.tenant_id == task.tenant_id,
                    TaskExecution.is_deleted.is_(False),
                )
                .with_for_update()
            )
            if row is None:
                return 0
            if (
                row.status != str(TaskStatus.WAITING_EXTERNAL)
                or row.wait_kind != WaitKind.ASYNC_OPERATION.value
            ):
                await self._record_late(session, row, views, moment)
                return 0
            current = watermarks_from_payload(row.wait_payload_json)
            merged = advance_watermarks(current, views)
            row.update_time = moment
            if merged == current:
                return 0
            row.wait_payload_json = payload_from_watermarks(merged)
            logger.info(
                "async_result_watermark_advanced",
                extra={
                    "execution_id": str(row.id),
                    "tenant_id": row.tenant_id,
                    "all_settled": all_settled(merged),
                },
            )
            return 1

    async def _rotate(self, task: TaskExecution, moment: datetime) -> None:
        """扫过但没推进的行也要轮转，否则它会永久占住队头（不改任何业务事实）。"""
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(TaskExecution)
                .where(
                    TaskExecution.id == task.id,
                    TaskExecution.tenant_id == task.tenant_id,
                    TaskExecution.status == str(TaskStatus.WAITING_EXTERNAL),
                    TaskExecution.is_deleted.is_(False),
                )
                .values(update_time=moment)
            )

    async def _record_late(
        self,
        session: AsyncSession,
        row: TaskExecution,
        views: Mapping[str, TaskToolOperationView],
        moment: datetime,
    ) -> None:
        """终态执行上迟到的结果只留审计事实：不改状态、不回到 QUEUED、不消耗重试。"""
        if row.status not in TERMINAL_STATUSES:
            return
        seeds = [
            TaskEventSeed(
                tenant_id=row.tenant_id,
                entity_type=EntityType.ASYNC,
                entity_id=view.operation_id,
                event_type=TaskEventType.RESULT_RECEIVED,
                occurred_at=moment,
                payload={
                    "execution_id": str(row.id),
                    "call_id": watermark.call_id,
                    "execution_status": row.status,
                    "terminal_status": (
                        view.result.terminal_status.value if view.result is not None else None
                    ),
                    "event_id": str(view.result.event_id) if view.result is not None else None,
                    "late": True,
                },
            )
            for watermark in watermarks_from_payload(row.wait_payload_json)
            if (view := views.get(watermark.call_id)) is not None and view.result is not None
        ]
        if not seeds:
            return
        await append_events(session, seeds)
        logger.info(
            "async_result_late_recorded",
            extra={"execution_id": str(row.id), "tenant_id": row.tenant_id, "count": len(seeds)},
        )


__all__ = ["AsyncResultSweeper"]
