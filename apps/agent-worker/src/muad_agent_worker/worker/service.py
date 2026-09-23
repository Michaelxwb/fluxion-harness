from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from muad_common import SharedSettings
from muad_contracts import TaskStatus
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.batch_fanin import settle_child
from ..application.task_cancel import cancel_children
from ..application.task_events import TaskEventSeed, TaskEventType, append_event, append_events
from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.models.task import TaskExecution
from ..infrastructure.wakeup_hint import NullWakeupListener, WakeupListener
from ..metrics import increment
from .claimer import TaskClaimer
from .execution_outcomes import DEFAULT_WAIT_SEC, OutcomeKind, TaskOutcome, interpret_execution
from .executor import SkillTaskExecutor, TaskExecutionError, TaskExecutorProtocol

logger = logging.getLogger(__name__)

RETRY_BACKOFF_BASE_SEC = 5
TASK_RECLAIM_TOTAL = "task_reclaim_total"


def default_instance_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _describe_error(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, TaskExecutionError):
        return exc.code, str(exc), exc.retryable
    return type(exc).__name__, str(exc), True


class WorkerLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SharedSettings | None = None,
        executor: TaskExecutorProtocol | None = None,
        claimer: TaskClaimer | None = None,
        instance_id: str | None = None,
        cancel_hints: CancelHintStore | None = None,
        wakeup: WakeupListener | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or SharedSettings()
        self._executor = executor
        self._claimer = claimer or TaskClaimer(session_factory, self._settings)
        self._instance_id = instance_id or default_instance_id()
        self._cancel_hints: CancelHintStore = cancel_hints or NullCancelHintStore()
        self._wakeup: WakeupListener = wakeup or NullWakeupListener()

    def _resolve_executor(self) -> TaskExecutorProtocol:
        if self._executor is None:
            # 延迟到真正执行时才构造：bootstrap 在导入时创建 artifact/cache 目录，
            # 只做 claim/reclaim 的 WorkerLoop 不该有文件系统副作用。
            from ..application.batch_fanout import BatchFanoutService
            from ..bootstrap.artifacts import skill_artifact_cache

            self._executor = SkillTaskExecutor(
                skill_artifact_cache,
                fanout=BatchFanoutService(self._session_factory, self._settings),
            )
        return self._executor

    @property
    def instance_id(self) -> str:
        return self._instance_id

    async def run_forever(self) -> None:
        """有活就连续处理；空闲时按 poll 间隔等待，`task:wakeup` hint 可提前唤醒。"""
        while True:
            processed: uuid.UUID | None = None
            try:
                processed = await self.run_once()
                await self.reclaim_expired()
            except Exception:
                logger.exception("worker_loop_tick_failed")
            if processed is None:
                await self._wakeup.wait(self._settings.worker_poll_interval_sec)

    async def run_once(self, *, now: datetime | None = None) -> uuid.UUID | None:
        task = await self._claimer.claim_one(self._instance_id, now=now)
        if task is None:
            return None
        cancelled = asyncio.Event()
        execution = asyncio.create_task(self._resolve_executor().execute(task))
        heartbeat = asyncio.create_task(self._heartbeat(task.id, execution, cancelled))
        try:
            result = await execution
        except asyncio.CancelledError:
            await self._stop_heartbeat(heartbeat)
            if cancelled.is_set():
                # 心跳检查点发现取消/失约：executor 已终止子进程，按取消收尾。
                await self._mark_cancelled_outcome(task, now=now)
                return task.id
            raise
        except Exception as exc:
            await self._stop_heartbeat(heartbeat)
            await self._handle_failure(task, exc, now=now)
            return task.id
        await self._stop_heartbeat(heartbeat)
        await self._handle_outcome(
            task, interpret_execution(result, now=now or datetime.now(UTC)), now=now
        )
        return task.id

    async def _handle_outcome(
        self,
        task: TaskExecution,
        outcome: TaskOutcome,
        *,
        now: datetime | None,
    ) -> None:
        if outcome.kind is OutcomeKind.COMPLETED:
            await self._handle_success(
                task,
                outcome.result or {},
                now=now,
                result_artifact_id=outcome.result_artifact_id,
            )
            return
        if outcome.kind is OutcomeKind.WAITING:
            await self._wait(task, outcome, now=now)
            return
        if outcome.kind is OutcomeKind.CANCELLED:
            moment = now or datetime.now(UTC)
            async with self._session_factory() as session:
                async with session.begin():
                    await self._mark_cancelled(session, task, moment)
            return
        await self._handle_failure(
            task,
            TaskExecutionError(outcome.error_code or "SKILL_EXECUTION_FAILED", outcome.error_message),
            now=now,
        )

    async def _wait(
        self,
        task: TaskExecution,
        outcome: TaskOutcome,
        *,
        now: datetime | None,
    ) -> None:
        """外部等待：记录 external_ref/not_before 并释放 lease，由 Scheduler 到期再 claim。"""
        moment = now or datetime.now(UTC)
        not_before = outcome.not_before or moment + timedelta(seconds=DEFAULT_WAIT_SEC)
        async with self._session_factory() as session:
            async with session.begin():
                rowcount = await self._cas(
                    session,
                    task.id,
                    {
                        "status": str(TaskStatus.WAITING),
                        "external_ref_json": outcome.external_ref or {},
                        "not_before": not_before,
                        "lease_owner": None,
                        "lease_until": None,
                        "update_time": moment,
                    },
                    moment=moment,
                    require_not_cancelled=True,
                )
                if rowcount != 1:
                    # 执行期间已被请求取消：按取消收尾，不能留成无 lease 的 RUNNING。
                    await self._mark_cancelled(session, task, moment)
                    return
                await append_events(
                    session,
                    [
                        TaskEventSeed(
                            tenant_id=task.tenant_id,
                            task_id=task.id,
                            event_type=TaskEventType.WAITING,
                            payload={
                                "external_ref": outcome.external_ref or {},
                                "not_before": not_before.isoformat(),
                                "attempt": task.attempt,
                            },
                        )
                    ],
                )

    async def reclaim_expired(self, *, now: datetime | None = None) -> int:
        """lease 过期的 RUNNING：未取消的置回 QUEUED（保留 attempt，返回其数量）；已请求取消的直接收尾。

        设计 reclaim 条件排除 `cancel_requested=true`，但持有者已崩溃时没有 Worker
        会再走到检查点——若不在这里收尾，它会卡在 RUNNING 直到 24h deadline 被判
        `TASK_DEADLINE_EXCEEDED`，而用户看到的应是 CANCELLED。
        """
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                rows = await self._requeue_expired(session, moment)
                await self._cancel_abandoned(session, moment)
        if rows:
            increment(TASK_RECLAIM_TOTAL, rows)
        return rows

    async def _requeue_expired(self, session: AsyncSession, moment: datetime) -> int:
        rows = (
            await session.execute(
                update(TaskExecution)
                .where(
                    TaskExecution.status == str(TaskStatus.RUNNING),
                    TaskExecution.lease_until < moment,
                    TaskExecution.cancel_requested.is_(False),
                    TaskExecution.is_deleted.is_(False),
                )
                .values(
                    status=str(TaskStatus.QUEUED),
                    lease_owner=None,
                    lease_until=None,
                    update_time=moment,
                )
                .returning(TaskExecution.id, TaskExecution.tenant_id)
            )
        ).all()
        await append_events(
            session,
            [
                TaskEventSeed(tenant_id=tenant_id, task_id=task_id, event_type=TaskEventType.RECLAIMED)
                for task_id, tenant_id in rows
            ],
        )
        return len(rows)

    async def _cancel_abandoned(self, session: AsyncSession, moment: datetime) -> int:
        tasks = list(
            (
                await session.execute(
                    update(TaskExecution)
                    .where(
                        TaskExecution.status == str(TaskStatus.RUNNING),
                        TaskExecution.lease_until < moment,
                        TaskExecution.cancel_requested.is_(True),
                        TaskExecution.is_deleted.is_(False),
                    )
                    .values(
                        status=str(TaskStatus.CANCELLED),
                        lease_owner=None,
                        lease_until=None,
                        finished_at=moment,
                        update_time=moment,
                    )
                    .returning(TaskExecution)
                    .execution_options(synchronize_session=False)
                )
            ).scalars()
        )
        for task in tasks:
            await append_event(
                session,
                tenant_id=task.tenant_id,
                task_id=task.id,
                event_type=TaskEventType.CANCELLED,
                payload={"reason": "lease_expired_after_cancel_request"},
            )
            await cancel_children(session, task, moment)
            await settle_child(session, task, moment)
        return len(tasks)

    async def _handle_success(
        self,
        task: TaskExecution,
        result: dict[str, Any],
        *,
        now: datetime | None,
        result_artifact_id: uuid.UUID | None = None,
    ) -> None:
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                rowcount = await self._cas(
                    session,
                    task.id,
                    {
                        "status": str(TaskStatus.COMPLETED),
                        "result_json": result,
                        "result_artifact_id": result_artifact_id,
                        "finished_at": moment,
                        "lease_owner": None,
                        "lease_until": None,
                        "update_time": moment,
                    },
                    moment=moment,
                    require_not_cancelled=True,
                )
                if rowcount == 1:
                    await append_events(
                        session,
                        [
                            TaskEventSeed(
                                tenant_id=task.tenant_id,
                                task_id=task.id,
                                event_type=TaskEventType.COMPLETED,
                                payload={"attempt": task.attempt},
                            )
                        ],
                    )
                    await settle_child(session, task, moment)
                    return
                # 已被请求取消：不覆写取消标记，按取消收尾（用户在结果出来前已经喊停）
                await self._mark_cancelled(session, task, moment)

    async def _handle_failure(
        self,
        task: TaskExecution,
        exc: Exception,
        *,
        now: datetime | None,
    ) -> None:
        moment = now or datetime.now(UTC)
        error_code, error_message, retryable = _describe_error(exc)
        async with self._session_factory() as session:
            async with session.begin():
                if retryable and task.attempt < task.max_attempts:
                    if await self._schedule_retry(session, task, error_code, error_message, moment):
                        return
                elif await self._mark_failed(session, task, error_code, error_message, moment):
                    return
                await self._mark_cancelled(session, task, moment)

    async def _schedule_retry(
        self,
        session: AsyncSession,
        task: TaskExecution,
        error_code: str,
        error_message: str,
        moment: datetime,
    ) -> bool:
        not_before = moment + timedelta(seconds=RETRY_BACKOFF_BASE_SEC * 2 ** (task.attempt - 1))
        rowcount = await self._cas(
            session,
            task.id,
            {
                "status": str(TaskStatus.QUEUED),
                "lease_owner": None,
                "lease_until": None,
                "not_before": not_before,
                "error_code": error_code,
                "error_message": error_message,
                "update_time": moment,
            },
            moment=moment,
            require_not_cancelled=True,
        )
        if rowcount != 1:
            return False
        await append_events(
            session,
            [
                TaskEventSeed(
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    event_type=TaskEventType.RETRY,
                    payload={
                        "attempt": task.attempt,
                        "not_before": not_before.isoformat(),
                        "error_code": error_code,
                    },
                )
            ],
        )
        return True

    async def _mark_failed(
        self,
        session: AsyncSession,
        task: TaskExecution,
        error_code: str,
        error_message: str,
        moment: datetime,
    ) -> bool:
        rowcount = await self._cas(
            session,
            task.id,
            {
                "status": str(TaskStatus.FAILED),
                "lease_owner": None,
                "lease_until": None,
                "finished_at": moment,
                "error_code": error_code,
                "error_message": error_message,
                "update_time": moment,
            },
            moment=moment,
            require_not_cancelled=True,
        )
        if rowcount != 1:
            return False
        await append_events(
            session,
            [
                TaskEventSeed(
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    event_type=TaskEventType.FAILED,
                    payload={"attempt": task.attempt, "error_code": error_code},
                )
            ],
        )
        await settle_child(session, task, moment)
        return True

    async def _mark_cancelled(
        self,
        session: AsyncSession,
        task: TaskExecution,
        moment: datetime,
    ) -> bool:
        rowcount = await self._cas(
            session,
            task.id,
            {
                "status": str(TaskStatus.CANCELLED),
                "cancel_requested": True,
                "lease_owner": None,
                "lease_until": None,
                "finished_at": moment,
                "update_time": moment,
            },
            moment=moment,
        )
        if rowcount != 1:
            return False
        await append_events(
            session,
            [
                TaskEventSeed(
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    event_type=TaskEventType.CANCELLED,
                    payload={"attempt": task.attempt},
                )
            ],
        )
        await cancel_children(session, task, moment)
        await settle_child(session, task, moment)
        return True

    async def _cas(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        values: dict[str, Any],
        *,
        moment: datetime,
        require_not_cancelled: bool = False,
    ) -> int:
        conditions: list[Any] = [
            TaskExecution.id == task_id,
            TaskExecution.status == str(TaskStatus.RUNNING),
            TaskExecution.lease_owner == self._instance_id,
            # 租约失效即失约：即使还没被 reclaim，持有者也不能再写状态，
            # 否则一个卡住后恢复的 Worker 会用过期结果覆盖别人的执行。
            TaskExecution.lease_until > moment,
            TaskExecution.is_deleted.is_(False),
        ]
        if require_not_cancelled:
            conditions.append(TaskExecution.cancel_requested.is_(False))
        result = await session.execute(update(TaskExecution).where(*conditions).values(**values))
        return int(cast(CursorResult[Any], result).rowcount)

    async def _mark_cancelled_outcome(self, task: TaskExecution, *, now: datetime | None) -> None:
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                await self._mark_cancelled(session, task, moment)

    async def _heartbeat(
        self,
        task_id: uuid.UUID,
        execution: asyncio.Task[dict[str, Any]] | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> None:
        """续租并检查取消标记。

        每 `task_heartbeat_sec` 续租一次并读 PG 取消标记（权威源，Redis 不可用时照常
        生效）；两次续租之间每 `task_cancel_check_sec` 看一眼 `task:cancel:{id}` hint，
        命中就立即读 PG 确认。一旦用户已请求取消、或租约已不属于本实例（被回收/已终态），
        立即叫停本地执行——这就是「检查点停止」。
        """
        step = max(1, min(self._settings.task_cancel_check_sec, self._settings.task_heartbeat_sec))
        elapsed = 0
        while True:
            await asyncio.sleep(step)
            elapsed += step
            hinted = await self._cancel_hints.is_marked(task_id)
            if not hinted and elapsed < self._settings.task_heartbeat_sec:
                continue
            elapsed = 0
            owned, requested = await self._renew_lease(task_id)
            if execution is None:
                continue
            if requested or not owned:
                if cancelled is not None:
                    cancelled.set()
                execution.cancel()
                return

    async def _renew_lease(self, task_id: uuid.UUID) -> tuple[bool, bool]:
        """续租并回读取消标记，返回 `(仍持有 lease, 已请求取消)`。"""
        moment = datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                heartbeat_result = await session.execute(
                    update(TaskExecution)
                    .where(
                        TaskExecution.id == task_id,
                        TaskExecution.status == str(TaskStatus.RUNNING),
                        TaskExecution.lease_owner == self._instance_id,
                        TaskExecution.is_deleted.is_(False),
                    )
                    .values(
                        heartbeat_at=moment,
                        lease_until=moment + timedelta(seconds=self._settings.task_lease_sec),
                        update_time=moment,
                    )
                )
                rowcount = int(cast(CursorResult[Any], heartbeat_result).rowcount)
                requested = await session.scalar(
                    select(TaskExecution.cancel_requested).where(TaskExecution.id == task_id)
                )
        return rowcount == 1, bool(requested)

    async def _stop_heartbeat(self, heartbeat: asyncio.Task[None]) -> None:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("worker_heartbeat_failed", extra={"instance_id": self._instance_id})
