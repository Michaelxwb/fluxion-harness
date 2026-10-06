from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from muad_common import SharedSettings
from muad_contracts import TaskStatus
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.batch_fanin import settle_child
from ..application.ports import NullPlatformSettingsClient, PlatformSettingsClient
from ..application.task_cancel import cancel_children
from ..application.task_events import TaskEventSeed, TaskEventType, append_event, append_events
from ..infrastructure.cancel_hint import CancelHintStore, NullCancelHintStore
from ..infrastructure.models.task import TaskExecution
from ..infrastructure.wakeup_hint import NullWakeupListener, WakeupListener
from ..metrics import (
    TASK_LEASE_EXPIRED_METRIC,
    TASK_RECLAIM_METRIC,
    TASKS_METRIC,
    increment,
    record_outcome,
)
from ..scheduler.service import TASK_DEADLINE_EXCEEDED
from .claimer import TaskClaimer
from .execution_outcomes import DEFAULT_WAIT_SEC, OutcomeKind, TaskOutcome, interpret_execution
from .executor import SkillTaskExecutor, TaskExecutionError, TaskExecutorProtocol

logger = logging.getLogger(__name__)

RETRY_BACKOFF_BASE_SEC = 5
#: 回收时重试预算已耗尽（`attempt >= max_attempts`）：终态失败，不再置回 QUEUED。
TASK_ATTEMPTS_EXHAUSTED = "TASK_ATTEMPTS_EXHAUSTED"
#: 心跳彻底失联（续租连续失败/失约）时执行器的收尾码——可重试，但本地执行必须先停。
HEARTBEAT_LOST_CODE = "WORKER_HEARTBEAT_LOST"
#: 续租是瞬时故障（DB 抖动）还是真失约，要分得开：前者有界重试，后者立刻停。
HEARTBEAT_RENEW_ATTEMPTS = 3
HEARTBEAT_RENEW_RETRY_SEC = 1.0


def default_instance_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _describe_error(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, TaskExecutionError):
        return exc.code, str(exc), exc.retryable
    return type(exc).__name__, str(exc), True


def _reclaim_backoff() -> Any:
    """`RETRY_BACKOFF_BASE_SEC * 2**(attempt-1)`，与 `_schedule_retry` 同一口径。

    批量 UPDATE 里 attempt 是**行内值**，只能在 SQL 里算；`make_interval(secs => …)`
    的写法与投递退避（`delivery/service.py`）一致。
    """
    return sa.func.make_interval(
        0, 0, 0, 0, 0, 0, RETRY_BACKOFF_BASE_SEC * sa.func.power(2, TaskExecution.attempt - 1)
    )


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
        settings_client: PlatformSettingsClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or SharedSettings()
        self._executor = executor
        self._claimer = claimer or TaskClaimer(session_factory, self._settings)
        self._instance_id = instance_id or default_instance_id()
        self._cancel_hints: CancelHintStore = cancel_hints or NullCancelHintStore()
        self._wakeup: WakeupListener = wakeup or NullWakeupListener()
        self._settings_client: PlatformSettingsClient = (
            settings_client or NullPlatformSettingsClient()
        )

    def _resolve_executor(self) -> TaskExecutorProtocol:
        if self._executor is None:
            # 延迟到真正执行时才构造：bootstrap 在导入时创建 artifact/cache 目录，
            # 只做 claim/reclaim 的 WorkerLoop 不该有文件系统副作用。
            from ..application.batch_fanout import BatchFanoutService
            from ..bootstrap.artifacts import skill_artifact_cache

            self._executor = SkillTaskExecutor(
                skill_artifact_cache,
                fanout=BatchFanoutService(
                    self._session_factory,
                    self._settings,
                    settings_client=self._settings_client,
                ),
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
        moment = now or datetime.now(UTC)
        cancelled = asyncio.Event()
        execution = asyncio.create_task(self._resolve_executor().execute(task))
        heartbeat = asyncio.create_task(self._heartbeat(task.id, execution, cancelled))
        try:
            await self._supervise(execution, heartbeat)
            if not execution.done():
                # 心跳先退出：本地执行已脱离租约监控，先叫停它再谈结局（评审 #3）。
                await self._abort_execution(execution)
                if cancelled.is_set():
                    await self._mark_cancelled_outcome(task, now=now)
                    return task.id
                raise TaskExecutionError(
                    HEARTBEAT_LOST_CODE, "lease heartbeat stopped before the skill finished"
                )
            result = execution.result()
            # 结果解析放在 try 内（评审 #14）：违反 Skill 协议的结果是**确定性**失败，必须
            # 和别的执行异常走同一条收尾（FAILED），而不是原地冒泡让任务卡在无心跳的 RUNNING。
            outcome = interpret_execution(result, now=moment)
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
        # 截止判定用**执行真正结束的时刻**，不是本轮 tick 的起点——Skill 可能跑了五分钟。
        finished_at = datetime.now(UTC)
        if finished_at >= task.deadline_at:
            # 结果产出时已过 deadline：不写 COMPLETED（评审 #8）。已有的副作用收不回，
            # 但「成功」这个结论不能落库。
            await self._mark_deadline_exceeded(task, now=finished_at)
            return task.id
        await self._handle_outcome(task, outcome, now=now)
        return task.id

    async def _supervise(
        self,
        execution: asyncio.Task[dict[str, Any]],
        heartbeat: asyncio.Task[None],
    ) -> None:
        """等到「执行结束」与「租约维护退出」中的**先者**。

        两者必须共同受监督：心跳一旦退出，执行器就再没有租约可依，而终态 CAS 只能拦住
        写库、拦不住已经产生的外部副作用（评审 #3 实测：心跳被一次 DB 错误打死，回收后
        旧执行仍然活着并继续建任务）。
        """
        await asyncio.wait({execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED)

    async def _abort_execution(self, execution: asyncio.Task[dict[str, Any]]) -> None:
        """叫停本地执行并等它真的结束（Skill 子进程组由执行器自己收尾）。

        执行器在这条路径上的返回值一律丢弃：**拿不出租约就没有资格写任何结论**，任务由
        回收者或新持有者推进。执行器抬起的异常同理只记日志，不改变这个判断。
        """
        execution.cancel()
        try:
            await execution
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("worker_execution_aborted_with_error", exc_info=True)

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
                await self._lock_parent(session, task)
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

        **两段提交、根任务在前**：子任务终态事务的加锁顺序是「父 → 子」（`_lock_parent`
        与 `settle_child`），回收若在同一事务里同时锁父与子，就与它形成循环等待。先只动
        根任务并提交，再动子任务——父锁在取子锁之前已经释放，环不存在。
        """
        moment = now or datetime.now(UTC)
        requeued = 0
        expired = 0  # 本轮的过期租约总数：回队列的 + 预算耗尽终态的 + 直接取消的
        for roots_only in (True, False):
            async with self._session_factory() as session:
                async with session.begin():
                    added, exhausted = await self._requeue_expired(
                        session, moment, roots_only=roots_only
                    )
                    requeued += added
                    expired += added + exhausted
                    expired += await self._cancel_abandoned(session, moment, roots_only=roots_only)
        if requeued:
            increment(TASK_RECLAIM_METRIC, requeued)
        if expired:
            # 预算耗尽而终态失败的也是一条「被观察到的过期租约」——不记它就等于把这次回收
            # 从指标面抹掉（降级必须可见）。
            increment(TASK_LEASE_EXPIRED_METRIC, expired)
        return requeued

    @staticmethod
    def _parent_scope(roots_only: bool) -> Any:
        return (
            TaskExecution.parent_id.is_(None)
            if roots_only
            else TaskExecution.parent_id.is_not(None)
        )

    async def _requeue_expired(
        self, session: AsyncSession, moment: datetime, *, roots_only: bool
    ) -> tuple[int, int]:
        """把租约过期的 RUNNING 送回队列——**但要先过重试预算这一关**（评审 #13）。

        此前回收无条件置回 QUEUED，而 claim 只递增 attempt、从不看上限：一个反复崩溃的
        任务可以在 deadline 之前无限重启。现在超过上限一律终态 `FAILED`，并且回收本身也
        按 `RETRY_BACKOFF_BASE_SEC * 2**(attempt-1)` 退避，不再立刻被再次领走。
        """
        base = (
            TaskExecution.status == str(TaskStatus.RUNNING),
            TaskExecution.lease_until < moment,
            TaskExecution.cancel_requested.is_(False),
            TaskExecution.is_deleted.is_(False),
            self._parent_scope(roots_only),
        )
        retryable = (
            await session.execute(
                update(TaskExecution)
                .where(*base, TaskExecution.attempt < TaskExecution.max_attempts)
                .values(
                    status=str(TaskStatus.QUEUED),
                    lease_owner=None,
                    lease_until=None,
                    not_before=moment + _reclaim_backoff(),
                    update_time=moment,
                )
                .returning(TaskExecution.id, TaskExecution.tenant_id)
            )
        ).all()
        await append_events(
            session,
            [
                TaskEventSeed(tenant_id=tenant_id, task_id=task_id, event_type=TaskEventType.RECLAIMED)
                for task_id, tenant_id in retryable
            ],
        )
        exhausted = await self._fail_exhausted(session, moment, base)
        return len(retryable), exhausted

    async def _fail_exhausted(
        self, session: AsyncSession, moment: datetime, base: tuple[Any, ...]
    ) -> int:
        """回收时重试预算已耗尽的行：终态失败，并照常触发 fan-in（子任务终态会让父任务聚合）。"""
        exhausted = list(
            (
                await session.execute(
                    update(TaskExecution)
                    .where(*base, TaskExecution.attempt >= TaskExecution.max_attempts)
                    .values(
                        status=str(TaskStatus.FAILED),
                        error_code=TASK_ATTEMPTS_EXHAUSTED,
                        error_message="attempts exhausted by crash reclaim",
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
        for task in exhausted:
            record_outcome(TASKS_METRIC, str(TaskStatus.FAILED), {"type": task.task_type})
            await append_event(
                session,
                tenant_id=task.tenant_id,
                task_id=task.id,
                event_type=TaskEventType.FAILED,
                payload={
                    "attempt": task.attempt,
                    "error_code": TASK_ATTEMPTS_EXHAUSTED,
                },
            )
            await settle_child(session, task, moment)
        return len(exhausted)

    async def _cancel_abandoned(
        self, session: AsyncSession, moment: datetime, *, roots_only: bool
    ) -> int:
        tasks = list(
            (
                await session.execute(
                    update(TaskExecution)
                    .where(
                        TaskExecution.status == str(TaskStatus.RUNNING),
                        TaskExecution.lease_until < moment,
                        TaskExecution.cancel_requested.is_(True),
                        TaskExecution.is_deleted.is_(False),
                        self._parent_scope(roots_only),
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
                await self._lock_parent(session, task)
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
                    record_outcome(
                        TASKS_METRIC,
                        str(TaskStatus.COMPLETED),
                        {"type": task.task_type},
                    )
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
                await self._lock_parent(session, task)
                if retryable and task.attempt < task.max_attempts:
                    if await self._schedule_retry(session, task, error_code, error_message, moment):
                        return
                elif await self._mark_failed(session, task, error_code, error_message, moment):
                    return
                await self._mark_cancelled(session, task, moment)

    async def _mark_deadline_exceeded(self, task: TaskExecution, *, now: datetime | None) -> None:
        """执行**已经跑过** deadline 时的收尾（评审 #8）。

        副作用收不回，但结论必须是失败：此前这条路径会照常写 `COMPLETED`，于是「超时的业务
        操作仍然做成了」在库里留下一个成功的样子。码与 Scheduler sweep 一致（同一常量），
        两条路径同形。
        """
        moment = now or datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                await self._lock_parent(session, task)
                rowcount = await self._cas(
                    session,
                    task.id,
                    {
                        "status": str(TaskStatus.FAILED),
                        "error_code": TASK_DEADLINE_EXCEEDED,
                        "error_message": "task deadline exceeded",
                        "finished_at": moment,
                        "lease_owner": None,
                        "lease_until": None,
                        "update_time": moment,
                    },
                    moment=moment,
                    require_not_cancelled=True,
                )
                if rowcount != 1:
                    return
                record_outcome(TASKS_METRIC, str(TaskStatus.FAILED), {"type": task.task_type})
                await append_events(
                    session,
                    [
                        TaskEventSeed(
                            tenant_id=task.tenant_id,
                            task_id=task.id,
                            event_type=TaskEventType.DEADLINE_EXCEEDED,
                            payload={"attempt": task.attempt},
                        )
                    ],
                )
                await settle_child(session, task, moment)

    async def _lock_parent(self, session: AsyncSession, task: TaskExecution) -> None:
        """终态写入前先取父行锁——**父 → 子**是父子事务唯一允许的加锁顺序（评审 #6）。

        取消路径（`cancel` 先锁 Parent 再 `cancel_children`）与本路径此前正好相反：本路径
        先 UPDATE 子行（持子锁）再在 `settle_child` 里锁父行，两条交错就构成 Parent→Child
        与 Child→Parent 的循环等待，PostgreSQL 实测判死锁并回滚一方（完成或取消的结果一起
        丢）。在子行 CAS **之前**拿父锁，环就不存在。
        """
        if task.parent_id is None:
            return
        await session.execute(
            select(TaskExecution.id).where(TaskExecution.id == task.parent_id).with_for_update()
        )

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
        await self._lock_parent(session, task)
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
        record_outcome(TASKS_METRIC, str(TaskStatus.FAILED), {"type": task.task_type})
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
        await self._lock_parent(session, task)
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
        record_outcome(TASKS_METRIC, str(TaskStatus.CANCELLED), {"type": task.task_type})
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
            renewal = await self._renew_with_retry(task_id)
            if renewal is None:
                # 有界重试也没拿到续租结论：**退出本任务**（异常交给 run_once 的监督者），
                # 让「执行器脱离租约监控」这件事不可能发生。
                raise TaskExecutionError(
                    HEARTBEAT_LOST_CODE, "lease renewal failed and could not be confirmed"
                )
            owned, requested = renewal
            if execution is None:
                continue
            if requested or not owned:
                if cancelled is not None:
                    cancelled.set()
                execution.cancel()
                return

    async def _renew_with_retry(self, task_id: uuid.UUID) -> tuple[bool, bool] | None:
        """续租的有界重试：瞬时 DB 抖动不该杀掉一个跑了五分钟的 Skill。

        重试耗尽仍无结论时返回 None（调用方退出心跳）；`(False, _)` 是**确定的**失约，
        不重试——那时租约已经不在我们手上，重试只会掩盖事实。
        """
        for attempt in range(HEARTBEAT_RENEW_ATTEMPTS):
            try:
                return await self._renew_lease(task_id)
            except Exception:
                logger.warning(
                    "worker_heartbeat_renew_failed",
                    extra={"instance_id": self._instance_id, "attempt": attempt + 1},
                    exc_info=True,
                )
                if attempt + 1 < HEARTBEAT_RENEW_ATTEMPTS:
                    await asyncio.sleep(HEARTBEAT_RENEW_RETRY_SEC)
        return None

    async def _renew_lease(self, task_id: uuid.UUID) -> tuple[bool, bool]:
        """续租并回读取消标记，返回 `(仍持有 lease, 已请求取消)`。

        续租条件与终态 CAS 同口径：**当前租约必须仍然有效**（`lease_until > now`）。少了
        这一格，进程/DB 卡顿超过租期之后，一次心跳就能把已经失效的执行权续到未来（评审
        #2 实测：旧实例续租成功、陈旧结果随后通过终态 CAS 写成 COMPLETED）。失约的实例
        只能停手，重新 claim 才能拿回执行权。
        """
        moment = datetime.now(UTC)
        async with self._session_factory() as session:
            async with session.begin():
                heartbeat_result = await session.execute(
                    update(TaskExecution)
                    .where(
                        TaskExecution.id == task_id,
                        TaskExecution.status == str(TaskStatus.RUNNING),
                        TaskExecution.lease_owner == self._instance_id,
                        TaskExecution.lease_until > moment,
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
