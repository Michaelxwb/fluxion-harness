"""PG claim：Schedule 策略下的 FIFO/并发、三类外部等待与两段式领取。

**两套集合必须分开**（design §3.5.3）：

- **阻断集**（Fire 创建 / `SKIP_IF_RUNNING`，`scheduler.fire.BLOCKING_ROOT_STATUSES`）包含 QUEUED；
- **占槽集**（claim 的 `QUEUE`/`ALLOW_PARALLEL`，本模块 `SLOT_STATUSES`）**不含 QUEUED** —— 含进去
  就等于让候选自己挡自己，或者让排在它前面的 QUEUED 让它永远领不到。

**领取分两步**：先无锁预筛（占槽计数 + 等待类型分发）挑一个候选，避免被挡住的队头每次都被选中
空转、挤掉别的 Schedule；再按固定锁序（Schedule → Execution，design §3.3.12）在 Schedule 行锁内
**复检**占槽数。预筛只减少空转，正确性在锁内复检——两个 Worker 可以同时通过预筛。

被挡住的候选**保持 QUEUED 且不写 `not_before`**：排队不是"未到期"，伪造退避会让 FIFO 与
重试退避混成一份。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_common import SharedSettings
from muad_contracts import ConcurrencyPolicy, TaskStatus, WaitKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from ..application.task_events import EntityType, TaskEventType, append_event
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.task import TaskExecution, TaskSchedule, TaskScheduleRevision
from ..metrics import TASK_QUEUE_DEPTH_METRIC, increment, record_gauge

TASK_CLAIM_TOTAL = "task_claim_total"

#: 占槽集：`QUEUE`/`ALLOW_PARALLEL` 的 claim 只看这三种状态；QUEUED 不在其中。
SLOT_STATUSES = (
    str(TaskStatus.RUNNING),
    str(TaskStatus.WAITING_EXTERNAL),
    str(TaskStatus.WAITING_HUMAN),
)
#: 定时唤醒即可领取的状态：QUEUED（首次/重试）与 WAITING_HUMAN（回答落库后置 `not_before`）。
TIMER_STATUSES = (str(TaskStatus.QUEUED), str(TaskStatus.WAITING_HUMAN))
#: `wait_payload_json.operations[]` 里每个 operation 的终态；**全部**终态才续接（design §3.5.6）。
WAIT_OPERATION_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
#: 匹配"仍有 operation 没到终态"的 jsonpath，由 `WAIT_OPERATION_TERMINAL` 生成（单一来源）。
_NON_TERMINAL_OPERATION_PATH = (
    "$.operations[*] ? ("
    + " && ".join(f'@.status != "{status}"' for status in WAIT_OPERATION_TERMINAL)
    + ")"
)
RESUME_REASON_HUMAN = "HUMAN"
RESUME_REASON_EXTERNAL = "EXTERNAL"
WAITING = str(TaskStatus.WAITING_EXTERNAL)


class JsonPath(sa.types.UserDefinedType):
    """`jsonpath` 字面量参数：`jsonb_path_exists` 的第二个实参必须是 jsonpath 类型。"""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "jsonpath"


def _jsonpath(path: str) -> Any:
    return sa.cast(sa.literal(path), JsonPath())


def _wait_dispatch(moment: datetime) -> Any:
    """`WAITING_EXTERNAL` 按 `wait_kind` 分发：三种等待的唤醒源不同，claim 口径也不同。

    - `EXTERNAL_POLL`：到 `not_before` 后带 `wait_payload_json.external_ref` 重入，由 Skill 自己轮询；
    - `ASYNC_OPERATION`：等结果 inbox 把 `operations[]` 的水位推到全部终态（`not_before` 不是唤醒源，
      定时器不得在结果没齐时把执行叫醒）；
    - `BATCH_FANIN`：没有定时唤醒源——Parent 由 Child 终态在 fan-in 事务里直接聚合终态，claim 永不领取。
    """
    operations = TaskExecution.wait_payload_json.op("->")("operations")
    return sa.or_(
        sa.and_(
            TaskExecution.wait_kind == WaitKind.EXTERNAL_POLL.value,
            TaskExecution.not_before <= moment,
        ),
        sa.and_(
            TaskExecution.wait_kind == WaitKind.ASYNC_OPERATION.value,
            sa.func.jsonb_typeof(operations) == "array",
            sa.func.jsonb_array_length(operations) > 0,
            sa.func.coalesce(
                sa.func.jsonb_path_exists(
                    TaskExecution.wait_payload_json, _jsonpath(_NON_TERMINAL_OPERATION_PATH)
                ),
                sa.false(),
            ).is_(sa.false()),
        ),
    )


def _claimable_conditions(moment: datetime) -> tuple[Any, ...]:
    """可 claim 条件：`task_queue_depth` 与 claim 本身共用同一口径。

    `deadline_at > moment` 是 2026-10-06 评审 #8 补上的一格：此前 claim 完全不看
    deadline，已过期的任务照样能被领走、跑完、写成 COMPLETED——超时的业务副作用真的
    发生了。过期行不在这里收尾（那是 Scheduler sweep 的职责，口径
    `FAILED(TASK_DEADLINE_EXCEEDED)`），这里只保证**不再执行**它。
    """
    timer_ready = sa.and_(
        TaskExecution.status.in_(TIMER_STATUSES),
        TaskExecution.not_before <= moment,
    )
    wait_ready = sa.and_(
        TaskExecution.status == WAITING,
        _wait_dispatch(moment),
    )
    return (
        sa.or_(timer_ready, wait_ready),
        TaskExecution.deadline_at > moment,
        TaskExecution.cancel_requested.is_(False),
        TaskExecution.is_deleted.is_(False),
    )


_OCCUPANT = aliased(TaskExecution)


def _occupants() -> Any:
    """同 Schedule 的占槽执行数：正是 `SLOT_STATUSES`，排除候选自身与全部 Child。

    `parent_id IS NULL` 排除 Child（Child 的并发由 fan-out 的停放名额管，不占 Schedule 槽）；
    `id <> 候选` 同时排除"自己的 `WAITING_EXTERNAL` 续接"——候选行本身就是那条等待。
    """
    return (
        sa.select(sa.func.count())
        .select_from(_OCCUPANT)
        .where(
            _OCCUPANT.tenant_id == TaskExecution.tenant_id,
            _OCCUPANT.schedule_id == TaskExecution.schedule_id,
            _OCCUPANT.parent_id.is_(None),
            _OCCUPANT.id != TaskExecution.id,
            _OCCUPANT.status.in_(SLOT_STATUSES),
            _OCCUPANT.is_deleted.is_(False),
        )
        .correlate(TaskExecution)
        .scalar_subquery()
    )


def _slot_limit() -> Any:
    """同 Schedule **当前 Revision** 的并发上限：`ALLOW_PARALLEL` 取 `max_concurrency`，其余为 1。

    上限只有一份来源（Revision `schedule_policy`），不写进 Execution；`ALLOW_PARALLEL` 必须真的
    按配置算出槽位，不能被一个笼统的 `NOT EXISTS` 降级成"永不并行"。
    """
    policy = TaskScheduleRevision.schedule_policy
    return sa.func.coalesce(
        sa.select(
            sa.case(
                (
                    policy.op("->>")("concurrency_policy") == ConcurrencyPolicy.ALLOW_PARALLEL.value,
                    sa.cast(policy.op("->>")("max_concurrency"), sa.Integer),
                ),
                else_=sa.literal(1),
            )
        )
        .select_from(TaskSchedule)
        .join(TaskScheduleRevision, TaskScheduleRevision.id == TaskSchedule.current_revision_id)
        .where(
            TaskSchedule.id == TaskExecution.schedule_id,
            TaskSchedule.is_deleted.is_(False),
            TaskScheduleRevision.is_deleted.is_(False),
        )
        .correlate(TaskExecution)
        .scalar_subquery(),
        sa.literal(1),
    )


def _slot_available() -> Any:
    """立即任务与 Batch Child 没有 Schedule 槽位；调度任务的占槽数必须低于策略上限。"""
    return sa.or_(TaskExecution.schedule_id.is_(None), _occupants() < _slot_limit())


def _queue_order() -> tuple[Any, ...]:
    """调度任务按 `(scheduled_at, create_time)` FIFO；立即任务保持 `(priority, create_time)`。

    立即任务（用户当场提交、有人在等）先于排队中的调度执行。两组的序互不干扰：调度行的
    第二键恒为 0（`priority` 不参与调度 FIFO），立即行的第三键恒为空（`scheduled_at` 不参与）。
    """
    scheduled = TaskExecution.scheduled_at.is_not(None)
    return (
        scheduled.asc(),
        sa.case((scheduled, sa.literal(0)), else_=TaskExecution.priority).asc(),
        TaskExecution.scheduled_at.asc(),
        TaskExecution.create_time.asc(),
    )


async def _claimable_depth(session: AsyncSession, moment: datetime) -> int:
    depth = await session.scalar(
        select(sa.func.count())
        .select_from(TaskExecution)
        .where(*_claimable_conditions(moment), _slot_available())
    )
    return int(depth or 0)


@dataclass(frozen=True, slots=True)
class ClaimCandidate:
    """预筛选出的候选：只带锁内复核需要的身份，不持有 Row 对象。"""

    id: uuid.UUID
    tenant_id: str
    schedule_id: uuid.UUID | None


class TaskClaimer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SharedSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or SharedSettings()

    async def claim_one(self, instance_id: str, *, now: datetime | None = None) -> TaskExecution | None:
        """领取一条可执行 Task：期限/可等待窗口按 PG 时钟判定（`now` 仅测试注入）。"""
        async with self._session_factory() as session:
            async with session.begin():
                claimed_at = now if now is not None else await database_now(session)
                record_gauge(TASK_QUEUE_DEPTH_METRIC, float(await _claimable_depth(session, claimed_at)))
                candidate = await self._prefilter(session, claimed_at)
                if candidate is None:
                    return None
                claimed = await self._claim_candidate(session, candidate, claimed_at, instance_id)
                if claimed is None:
                    return None
                task, resume_reason = claimed
                await self._append_claimed(session, task, claimed_at, instance_id, resume_reason)
                await session.flush()
        increment(TASK_CLAIM_TOTAL)
        return task

    async def _prefilter(self, session: AsyncSession, moment: datetime) -> ClaimCandidate | None:
        """无锁预筛（**不**是正确性保证）：只挑一条"看起来领得到"的候选。

        占槽计数与等待分发都写进 SQL，被挡住的队头不会被选中——否则一个 QUEUE Schedule 的
        排队头部会每拍都被选中、空转，把别的 Schedule 挤在后面。
        """
        row = (
            await session.execute(
                sa.select(
                    TaskExecution.id,
                    TaskExecution.tenant_id,
                    TaskExecution.schedule_id,
                )
                .where(*_claimable_conditions(moment), _slot_available())
                .order_by(*_queue_order())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        return ClaimCandidate(id=row[0], tenant_id=row[1], schedule_id=row[2])

    async def _claim_candidate(
        self,
        session: AsyncSession,
        candidate: ClaimCandidate,
        moment: datetime,
        instance_id: str,
    ) -> tuple[TaskExecution, str | None] | None:
        """锁内复检并改状态：**先 Schedule 再 Execution**（design §3.3.12 的固定锁序）。

        Schedule 行锁同时承担两件事：让同一 Schedule 的占槽计数在复检期间冻结（终态/回收/取消
        都在这把锁下改占槽集），并让两个同时通过预筛的 Worker 串行化。取不到行或复检不过就
        返回 None——本轮不领，候选保持原状。
        """
        if candidate.schedule_id is not None:
            locked = await session.scalar(
                sa.select(TaskSchedule.id)
                .where(
                    TaskSchedule.id == candidate.schedule_id,
                    TaskSchedule.tenant_id == candidate.tenant_id,
                    TaskSchedule.is_deleted.is_(False),
                )
                .with_for_update()
            )
            if locked is None:
                return None
            if not await self._slot_available_for(session, candidate.id):
                return None
        task = (
            await session.execute(
                sa.select(TaskExecution)
                .where(TaskExecution.id == candidate.id, *_claimable_conditions(moment))
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if task is None:
            return None
        return task, self._transition(task, moment, instance_id)

    async def _slot_available_for(self, session: AsyncSession, task_id: uuid.UUID) -> bool:
        """锁内复核占槽数：`NOT EXISTS` 预筛单独挡不住两个同时通过的 Worker。"""
        available = await session.scalar(
            sa.select(_slot_available()).select_from(TaskExecution).where(TaskExecution.id == task_id)
        )
        return bool(available)

    def _transition(self, task: TaskExecution, moment: datetime, instance_id: str) -> str | None:
        """QUEUED/WAITING → RUNNING：真实重试计 attempt，Human/External 续接只计 resume_count。"""
        resume_reason = {
            str(TaskStatus.WAITING_HUMAN): RESUME_REASON_HUMAN,
            WAITING: RESUME_REASON_EXTERNAL,
        }.get(task.status)
        task.status = str(TaskStatus.RUNNING)
        task.lease_epoch += 1
        task.lease_owner = instance_id
        task.lease_until = moment + timedelta(seconds=self._settings.task_lease_sec)
        task.heartbeat_at = moment
        # 状态不再是 WAITING_EXTERNAL；`wait_payload_json` 留着给执行器读 external_ref
        # （轮询续接靠它找回上次的外部句柄），不再是"等待中"的标记。
        task.wait_kind = None
        if resume_reason is None:
            task.attempt += 1
        else:
            task.resume_count += 1
            task.last_resume_reason = resume_reason
        # 只在首次开始执行时写一次；reclaim/续接都不得清空（"未开始"用 `started_at IS NULL`）。
        if task.started_at is None:
            task.started_at = moment
        task.update_time = moment
        return resume_reason

    async def _append_claimed(
        self,
        session: AsyncSession,
        task: TaskExecution,
        moment: datetime,
        instance_id: str,
        resume_reason: str | None,
    ) -> None:
        await append_event(
            session,
            tenant_id=task.tenant_id,
            entity_type=EntityType.EXECUTION,
            entity_id=task.id,
            event_type=TaskEventType.CLAIMED,
            occurred_at=moment,
            payload={
                "lease_owner": instance_id,
                "lease_epoch": task.lease_epoch,
                "attempt": task.attempt,
                "resume_count": task.resume_count,
                "lease_until": task.lease_until.isoformat() if task.lease_until else None,
                "resume_reason": resume_reason,
            },
        )
