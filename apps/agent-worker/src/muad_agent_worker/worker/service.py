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
from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.task_events import TaskEventSeed, TaskEventType, append_events
from ..infrastructure.models.task import TaskExecution
from .claimer import TaskClaimer
from .execution_outcomes import DEFAULT_WAIT_SEC, OutcomeKind, TaskOutcome, interpret_execution
from .executor import SkillTaskExecutor, TaskExecutionError, TaskExecutorProtocol

logger = logging.getLogger(__name__)

RETRY_BACKOFF_BASE_SEC = 5
TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
NON_TERMINAL_STATUSES = (
    str(TaskStatus.QUEUED),
    str(TaskStatus.RUNNING),
    str(TaskStatus.WAITING),
)


def default_instance_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _describe_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, TaskExecutionError):
        return exc.code, str(exc)
    return type(exc).__name__, str(exc)


class WorkerLoop:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SharedSettings | None = None,
        executor: TaskExecutorProtocol | None = None,
        claimer: TaskClaimer | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or SharedSettings()
        self._executor = executor
        self._claimer = claimer or TaskClaimer(session_factory, self._settings)
        self._instance_id = instance_id or default_instance_id()

    def _resolve_executor(self) -> TaskExecutorProtocol:
        if self._executor is None:
            # 延迟到真正执行时才构造：bootstrap 在导入时创建 artifact/cache 目录，
            # 只做 claim/reclaim 的 WorkerLoop 不该有文件系统副作用。
            from ..bootstrap.artifacts import skill_artifact_cache

            self._executor = SkillTaskExecutor(skill_artifact_cache)
        return self._executor

    @property
    def instance_id(self) -> str:
        return self._instance_id

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
                await self.reclaim_expired()
                await self.sweep_deadlines()
            except Exception:
                logger.exception("worker_loop_tick_failed")
            await asyncio.sleep(self._settings.worker_poll_interval_sec)

    async def run_once(self, *, now: datetime | None = None) -> uuid.UUID | None:
        task = await self._claimer.claim_one(self._instance_id, now=now)
        if task is None:
            return None
        heartbeat = asyncio.create_task(self._heartbeat(task.id))
        try:
            result = await self._resolve_executor().execute(task)
        except asyncio.CancelledError:
            await self._stop_heartbeat(heartbeat)
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
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
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
                        TaskEventSeed(
                            tenant_id=tenant_id,
                            task_id=task_id,
                            event_type=TaskEventType.RECLAIMED,
                        )
                        for task_id, tenant_id in rows
                    ],
                )
        return len(rows)

    async def sweep_deadlines(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        update(TaskExecution)
                        .where(
                            TaskExecution.status.in_(NON_TERMINAL_STATUSES),
                            TaskExecution.deadline_at < moment,
                            TaskExecution.is_deleted.is_(False),
                        )
                        .values(
                            status=str(TaskStatus.FAILED),
                            error_code=TASK_DEADLINE_EXCEEDED,
                            error_message="task deadline exceeded",
                            finished_at=moment,
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
                        TaskEventSeed(
                            tenant_id=tenant_id,
                            task_id=task_id,
                            event_type=TaskEventType.DEADLINE_EXCEEDED,
                        )
                        for task_id, tenant_id in rows
                    ],
                )
        return len(rows)

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

    async def _handle_failure(
        self,
        task: TaskExecution,
        exc: Exception,
        *,
        now: datetime | None,
    ) -> None:
        moment = now or datetime.now(UTC)
        error_code, error_message = _describe_error(exc)
        async with self._session_factory() as session:
            async with session.begin():
                if task.attempt < task.max_attempts:
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

    async def _heartbeat(self, task_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(self._settings.task_heartbeat_sec)
            moment = datetime.now(UTC)
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
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

    async def _stop_heartbeat(self, heartbeat: asyncio.Task[None]) -> None:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("worker_heartbeat_failed", extra={"instance_id": self._instance_id})
