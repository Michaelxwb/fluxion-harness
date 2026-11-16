"""Kernel 的回合 checkpoint 与等待判定（`ExecutionEventPort` 的 Worker 实现）。

四条口径：

- **落库形状由 Kernel 给出**：`RunnerCheckpoint.as_state()` 是唯一序列化，本模块只负责把它写进
  `task.task_execution_checkpoint` 并维护 `last_checkpoint_id`（两处都在同一事务）；
- **受租约栅栏保护**：只有仍持有该 `lease_epoch` 且租约未过期的执行方可以写 checkpoint；
  失约即 `AgenticLeaseLost`（`RunnerCancelled` 的子类），Kernel 据此停止本地执行；
- **停止本地执行 ≠ 写终态**：终态由 WorkerLoop 的 CAS 收口；本模块从不写 Execution 状态；
- 等待状态（`WAITING_EXTERNAL` / `WAITING_HUMAN`）**不在执行期写**——那时的写入必然与心跳续租
  抢同一行。Kernel 把挂起边界的 checkpoint 交给执行器，由 WorkerLoop 在停下心跳之后一次事务写入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from muad_agent_core.agent import RunnerCancelled
from muad_agent_core.agent.continuation import ExecutionEventBatch, RunnerCheckpoint, WaitDecision
from muad_contracts import OperationWatermark
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.db_clock import database_now
from ...infrastructure.models.task import TaskExecution, TaskExecutionCheckpoint

CHECKPOINT_SCHEMA_VERSION = 1


class AgenticLeaseLost(RunnerCancelled):
    """本执行方已失约（owner/epoch/租约/状态不符）：停止本地执行，不写任何结论。"""


def checkpoint_state(progress: RunnerCheckpoint) -> dict[str, Any]:
    """checkpoint 的持久形态：Kernel 的 `as_state()` 加上版本号。"""
    return {"schema_version": CHECKPOINT_SCHEMA_VERSION, **progress.as_state()}


def checkpoint_from_state(state: Mapping[str, Any] | None) -> RunnerCheckpoint:
    """从落库形态恢复 checkpoint：缺失/坏形状按空 checkpoint（恢复重新开始，不臆造进度）。"""
    if not isinstance(state, Mapping):
        return RunnerCheckpoint()
    pending = tuple(
        OperationWatermark.model_validate(entry)
        for entry in state.get("pending_tool_calls") or ()
        if isinstance(entry, Mapping)
    )
    return RunnerCheckpoint(
        turns=_count(state.get("turns")),
        tool_calls=_count(state.get("tool_calls")),
        input_tokens=_optional_count(state.get("input_tokens")),
        output_tokens=_optional_count(state.get("output_tokens")),
        consumed_event_seq=_count(state.get("consumed_event_seq")),
        pending_tool_calls=pending,
    )


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _optional_count(value: Any) -> int | None:
    return None if value is None else _count(value)


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """本执行方：与 claim 写入的 owner/epoch 完全一致。"""

    tenant_id: str
    execution_id: UUID
    owner: str
    epoch: int


class TaskCheckpointStore:
    """`ExecutionEventPort` 的 Worker 实现（回合 checkpoint + 结果注入 + 等待判定）。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        identity: ExecutionIdentity,
    ) -> None:
        self._factory = factory
        self._identity = identity

    async def drain_ready(self, cursor: int, limit: int) -> ExecutionEventBatch:
        """Worker 没有独立入站事件流：异步结果在**恢复时**替换进对应 `call_id` 的 tool 消息。

        这一点与 Runtime 不同（它的 canonical_event 会在轮次之间追加消息）：Task 的结果属于
        Task-owned 操作收件箱，恢复由 `load_resume_state` 一次性注入，Kernel 这里没有新消息。
        """
        return ExecutionEventBatch((), cursor)

    async def checkpoint(self, checkpoint: RunnerCheckpoint) -> None:
        await self.persist(checkpoint)

    async def try_wait(self, checkpoint: RunnerCheckpoint, *, assistant_text: str) -> WaitDecision:
        """Worker 不在这个入口进入等待：`WAITING_TOOL` 由工具级挂起（ASYNC 提交）表达。

        工具级挂起在完整回合收口时就已经收口了整个执行（lease 由 WorkerLoop 释放），
        所以走到这里的只有"没有待定操作"的收口：落 checkpoint 后结束。
        """
        await self.persist(checkpoint)
        return WaitDecision.FINISH

    async def persist(self, checkpoint: RunnerCheckpoint) -> None:
        """写一条 checkpoint（同一事务里推进 `last_checkpoint_id`），受租约栅栏保护。"""
        async with self._factory() as session, session.begin():
            moment = await database_now(session)
            await self._locked(session, moment)
            latest = await session.scalar(
                select(TaskExecutionCheckpoint)
                .where(
                    TaskExecutionCheckpoint.execution_id == self._identity.execution_id,
                    TaskExecutionCheckpoint.tenant_id == self._identity.tenant_id,
                )
                .order_by(TaskExecutionCheckpoint.checkpoint_no.desc())
                .limit(1)
                .with_for_update()
            )
            checkpoint_no = (latest.checkpoint_no if latest is not None else 0) + 1
            row = TaskExecutionCheckpoint(
                tenant_id=self._identity.tenant_id,
                execution_id=self._identity.execution_id,
                checkpoint_no=checkpoint_no,
                state_json=checkpoint_state(checkpoint),
            )
            session.add(row)
            await session.flush()
            await session.execute(
                update(TaskExecution)
                .where(
                    TaskExecution.id == self._identity.execution_id,
                    TaskExecution.tenant_id == self._identity.tenant_id,
                )
                .values(last_checkpoint_id=row.id, update_time=moment)
            )

    async def _locked(self, session: AsyncSession, moment: Any) -> None:
        """栅栏：状态/owner/epoch/租约/取消全在一行上复检，任一不符即失约。"""
        row = (
            await session.execute(
                select(
                    TaskExecution.status,
                    TaskExecution.lease_owner,
                    TaskExecution.lease_epoch,
                    TaskExecution.lease_until,
                    TaskExecution.cancel_requested,
                )
                .where(
                    TaskExecution.id == self._identity.execution_id,
                    TaskExecution.tenant_id == self._identity.tenant_id,
                    TaskExecution.is_deleted.is_(False),
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise AgenticLeaseLost("execution row missing")
        status, owner, epoch, lease_until, cancel_requested = row
        if (
            status != "RUNNING"
            or owner != self._identity.owner
            or epoch != self._identity.epoch
            or lease_until is None
            or lease_until <= moment
            or cancel_requested
        ):
            raise AgenticLeaseLost("checkpoint rejected: owner, epoch, lease or cancel changed")



__all__ = [
    "AgenticLeaseLost",
    "ExecutionIdentity",
    "TaskCheckpointStore",
    "checkpoint_from_state",
    "checkpoint_state",
]
