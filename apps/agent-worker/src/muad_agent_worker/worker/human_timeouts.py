"""Human 提问超时的独立 sweep（design §3.2.6 步骤 8 / §3.3.8 / §3.5.8）。

一条口径：**超时是独立写方**。回答只把 OPEN 改成 RESOLVED，取消只改 CANCELLED；只有本 sweep
写 `TIMED_OUT`，因此三者的 CAS 冲突是确定性的——都要求 `status=OPEN` / `status=WAITING_HUMAN`，
谁先拿到行锁谁生效，落败方整体回滚，永不把终态改回 QUEUED。

与 deadline sweep 的分工（`scheduler.service.DeadlineSweeper`）：

- deadline 是**执行**的绝对上限，到点写 `FAILED(TASK_DEADLINE_EXCEEDED)`；
- 本 sweep 看**问题**的 `deadline_at`（`_wait_human` 保证不晚于执行期限），写
  `CANCELLED(HUMAN_INTERACTION_EXPIRED)` + `failure_class=HUMAN_TIMEOUT`（design §3.2.6 /
  §3.5.8：超时按"取消"收尾）并关闭 interaction；
- 本 sweep 的执行 CAS 只认 `status=WAITING_HUMAN`，且**执行已终态时只关闭 interaction**
  （不让 deadline sweep 赢下的行留下一个永远 OPEN 的问题）。两边都不写对方的状态。

有界：每轮最多扫描 `SWEEP_BATCH` 条到期 interaction，逐条独立事务收尾，扫描不持锁跨越无关行。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from muad_contracts import HumanInteractionStatus, TaskStatus, TerminalStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.batch_fanin import settle_child
from ..application.task_events import EntityType, TaskEventSeed, TaskEventType, append_events
from ..application.terminal_tasks import TerminalChange, lock_task_tree, write_terminal
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.task import TaskExecution, TaskHumanInteraction
from ..metrics import TASK_HUMAN_TIMEOUT_METRIC, increment, record_loop_tick

logger = logging.getLogger("muad_agent_worker.worker.human_timeouts")

#: 失败分类（`FailureClass.HUMAN_TIMEOUT`）：与"取消"并列记录原因，不是状态值。
HUMAN_TIMEOUT = "HUMAN_TIMEOUT"
#: 终态错误码来自 `config/api-messages.yaml`（`HUMAN_INTERACTION_EXPIRED` = 410「人工确认已超时」）。
HUMAN_TIMEOUT_CODE = "HUMAN_INTERACTION_EXPIRED"
HUMAN_TIMEOUT_MESSAGE = "human interaction deadline exceeded"
#: 单轮扫描上界：超时收尾是短事务，批大小只为限制一次 tick 的工作量。
SWEEP_BATCH = 128
OPEN = str(HumanInteractionStatus.OPEN)
TIMED_OUT = str(HumanInteractionStatus.TIMED_OUT)
WAITING_HUMAN = str(TaskStatus.WAITING_HUMAN)


class HumanTimeoutSweeper:
    """到期 OPEN interaction 的收尾；`now` 只作测试注入，生产取 PG 时钟。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, batch: int = SWEEP_BATCH
    ) -> None:
        self._session_factory = session_factory
        self._batch = batch

    async def sweep(self, *, now: datetime | None = None) -> int:
        """返回本轮真正收尾的 interaction 数。"""
        async with self._session_factory() as session:
            moment = now if now is not None else await database_now(session)
            due = list(
                await session.execute(
                    select(
                        TaskHumanInteraction.id,
                        TaskHumanInteraction.tenant_id,
                        TaskHumanInteraction.execution_id,
                    )
                    .where(
                        TaskHumanInteraction.status == OPEN,
                        TaskHumanInteraction.deadline_at <= moment,
                        TaskHumanInteraction.is_deleted.is_(False),
                    )
                    .order_by(TaskHumanInteraction.deadline_at, TaskHumanInteraction.id)
                    .limit(self._batch)
                )
            )
        closed = 0
        for interaction_id, tenant_id, execution_id in due:
            async with self._session_factory() as session, session.begin():
                if await self._close_one(session, interaction_id, tenant_id, execution_id, moment):
                    closed += 1
        if closed:
            increment(TASK_HUMAN_TIMEOUT_METRIC, closed)
            logger.info("human_timeout_swept", extra={"closed": closed})
        return closed

    async def _close_one(
        self,
        session: AsyncSession,
        interaction_id: uuid.UUID,
        tenant_id: str,
        execution_id: uuid.UUID,
        moment: datetime,
    ) -> bool:
        execution = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.id == execution_id,
                TaskExecution.tenant_id == tenant_id,
                TaskExecution.is_deleted.is_(False),
            )
        )
        if execution is None:
            return False
        # 锁序与回答/取消一致：任务树在前，interaction 在后。
        await lock_task_tree(session, execution)
        interaction = await session.scalar(
            select(TaskHumanInteraction)
            .where(
                TaskHumanInteraction.id == interaction_id,
                TaskHumanInteraction.tenant_id == tenant_id,
                TaskHumanInteraction.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            interaction is None
            or interaction.status != OPEN
            or interaction.deadline_at > moment
        ):
            # 回答/取消已经赢下这一行；不是本轮的事实，不改写。
            return False
        await session.refresh(execution, with_for_update=True)
        if execution.status == WAITING_HUMAN:
            terminal = await self._fail_execution(session, execution, interaction_id, moment)
            if not terminal:
                # 执行仍在等待却写不进终态：保留 interaction，下一轮重试（不能关掉问题让执行空等）。
                return False
        else:
            logger.warning(
                "human_timeout_on_non_waiting_execution",
                extra={
                    "interaction_id": str(interaction_id),
                    "execution_id": str(execution_id),
                    "status": execution.status,
                },
            )
        return await self._close_interaction(session, interaction, moment)

    async def _fail_execution(
        self,
        session: AsyncSession,
        execution: TaskExecution,
        interaction_id: uuid.UUID,
        moment: datetime,
    ) -> bool:
        written = await write_terminal(
            session,
            execution,
            TerminalChange(
                status=TerminalStatus.CANCELLED,
                error_code=HUMAN_TIMEOUT_CODE,
                error_message=HUMAN_TIMEOUT_MESSAGE,
                failure_class=HUMAN_TIMEOUT,
            ),
            moment,
            conditions=(
                TaskExecution.status == WAITING_HUMAN,
                TaskExecution.cancel_requested.is_(False),
            ),
            event_payload={"reason": HUMAN_TIMEOUT, "interaction_id": str(interaction_id)},
        )
        if written:
            await settle_child(session, execution, moment)
        return written

    async def _close_interaction(
        self, session: AsyncSession, interaction: TaskHumanInteraction, moment: datetime
    ) -> bool:
        closed = await session.execute(
            update(TaskHumanInteraction)
            .where(
                TaskHumanInteraction.id == interaction.id,
                TaskHumanInteraction.tenant_id == interaction.tenant_id,
                TaskHumanInteraction.status == OPEN,
                TaskHumanInteraction.is_deleted.is_(False),
            )
            .values(status=TIMED_OUT, resolved_at=moment, update_time=moment)
            .returning(TaskHumanInteraction.id)
            .execution_options(synchronize_session=False)
        )
        if closed.one_or_none() is None:
            return False
        await append_events(
            session,
            [
                TaskEventSeed(
                    tenant_id=interaction.tenant_id,
                    entity_type=EntityType.HUMAN,
                    entity_id=interaction.id,
                    event_type=TaskEventType.TIMED_OUT,
                    occurred_at=moment,
                    payload={
                        "execution_id": str(interaction.execution_id),
                        "deadline_at": interaction.deadline_at.isoformat(),
                    },
                )
            ],
        )
        return True


class HumanTimeoutLoop:
    """独立节拍：与 deadline sweep 并列、互不排队（两者都不等外部调用）。"""

    def __init__(self, sweeper: HumanTimeoutSweeper, *, interval_sec: int) -> None:
        self._sweeper = sweeper
        self._interval_sec = interval_sec

    async def run_forever(self) -> None:
        while True:
            try:
                await self._sweeper.sweep()
            except Exception:
                logger.exception("human_timeout_sweep_failed")
                record_loop_tick("worker.human_timeouts", "failed")
            else:
                record_loop_tick("worker.human_timeouts", "ok")
            await asyncio.sleep(self._interval_sec)


__all__ = [
    "HUMAN_TIMEOUT",
    "HUMAN_TIMEOUT_CODE",
    "HumanTimeoutLoop",
    "HumanTimeoutSweeper",
    "SWEEP_BATCH",
]
