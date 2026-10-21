"""有界执行槽：本地有槽才 PG claim，每个槽独立监督、独立回收。

- **本地槽先于 PG 领取**：`claim_and_submit` 先看本进程的空闲槽，没有槽就直接返回 False，
  `claim` 一次都不会被调用（design §3.2「每个工作进程先获得本地槽，再在短事务中 claim；
  没有槽不领取」）。这把原先"每 tick 最多领 16 条、总量不设上限"的无界并发换成有界监督。
- **每个槽独立监督**：一个片段就是一个独立 asyncio 任务 + 独立完成回调；单槽卡死/超时/异常
  只占自己的槽，不阻塞其它槽，也不阻塞心跳/回收/控制等各自独立的循环。
- **槽释放与 Run 终态解耦**：片段结束（终态、WAITING_TOOL/WAITING_INPUT、租约丢失、异常）
  即释放槽；`reclaim_stale_executions` 按 PG 行事实回收那些"Run 已不再由本实例持有"的卡死槽。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from muad_contracts import RunStatus
from sqlalchemy import select

from ...infrastructure.db import SessionFactory
from ...infrastructure.db_clock import database_now
from ...infrastructure.models.runtime import RunRecord

logger = logging.getLogger(__name__)

Claim = Callable[
    [frozenset[UUID]], Awaitable[tuple[UUID, Callable[[], Awaitable[object]]] | None]
]


class ExecutionSupervisor:
    """有界执行槽：并发上限由装配根按 `RUNTIME_EXECUTION_CONCURRENCY` 注入。"""

    def __init__(self, *, max_concurrency: int = 8, close_timeout_sec: float = 10) -> None:
        if isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= 256:
            raise ValueError("invalid execution slot budget")
        if close_timeout_sec <= 0:
            raise ValueError("invalid supervisor close budget")
        self._limit = max_concurrency
        self._lock = asyncio.Lock()
        self._closing = False
        self._tasks: dict[UUID, asyncio.Task[object]] = {}
        #: 已占槽但仍在锁外做 claim 的数量：槽预算 = 运行中 + 在领，防止并发领取超发
        self._pending = 0
        self._timeout = close_timeout_sec

    @property
    def slot_limit(self) -> int:
        return self._limit

    @property
    def execution_count(self) -> int:
        return len(self._tasks)

    @property
    def free_slots(self) -> int:
        return max(self._limit - len(self._tasks) - self._pending, 0)

    def active_runs(self) -> frozenset[UUID]:
        """当前占槽的 Run 只读快照（独立监督回收据此核验 PG 归属）。"""
        return frozenset(self._tasks)

    def _has_free_slot(self) -> bool:
        return not self._closing and len(self._tasks) + self._pending < self._limit

    async def submit(self, run_id: UUID, execution: Callable[[], Awaitable[object]]) -> bool:
        async with self._lock:
            if not self._has_free_slot() or run_id in self._tasks:
                return False
            self._start(run_id, execution)
            return True

    async def claim_and_submit(self, claim: Claim) -> bool:
        """本地有槽才 claim：没有空闲槽时不触碰 PG（`claim` 不会被调用）。

        **claim 在锁外执行**：DB 往返（扫描 + 行锁 + 提交）在 800 用户下可达数十毫秒，
        持锁做它会把同 Pod 的所有领取与续跑泵串成一条队列（实测单次领取 P50 ~13s）。
        锁内只占一个"在领"坑位，claim 完成后回到锁内登记任务或释放坑位。
        """
        async with self._lock:
            if not self._has_free_slot():
                return False
            self._pending += 1
        admitted: tuple[UUID, Callable[[], Awaitable[object]]] | None = None
        submitted = False
        try:
            admitted = await claim(frozenset(self._tasks))
        finally:
            async with self._lock:
                self._pending -= 1
                if admitted is not None and not self._closing:
                    run_id, execution = admitted
                    if run_id not in self._tasks:
                        self._start(run_id, execution)
                        submitted = True
        return submitted

    def reclaim(self, run_id: UUID) -> bool:
        """回收单个槽：取消该 Run 的本地片段，完成后由完成回调释放槽。"""
        task = self._tasks.get(run_id)
        if task is None or task.done() or task.cancelling():
            return False
        task.cancel()
        return True

    def _start(self, run_id: UUID, execution: Callable[[], Awaitable[object]]) -> None:
        async def invoke() -> object:
            return await execution()

        task: asyncio.Task[object] = asyncio.create_task(invoke(), name=f"run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda completed: self._finished(run_id, completed))

    def _finished(self, run_id: UUID, task: asyncio.Task[object]) -> None:
        """单槽独立回收：成功/异常/取消都只释放本槽，不触碰其它槽。"""
        self._tasks.pop(run_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "supervised_run_failed",
                extra={"run_id": str(run_id), "error_type": type(error).__name__},
            )

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            tasks = tuple(self._tasks.values())
            for task in tasks:
                task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._timeout)
            if pending:
                logger.error("supervisor_close_timeout", extra={"pending_count": len(pending)})
                raise TimeoutError("run executions did not stop within close budget")
        self._tasks.clear()


async def reclaim_stale_executions(
    factory: SessionFactory,
    supervisor: ExecutionSupervisor,
    *,
    instance_id: str,
    now: datetime | None = None,
) -> int:
    """独立监督回收：取消已不再由本实例持有的本地片段，释放它们的槽。

    归属判据只用 **PG 行事实**（`RUNNING` + `lease_owner` 为本实例 + 租约未过期），租约
    比较取 **PG 服务端时钟**（B-235；`now` 仅测试注入）：本机墙钟偏差既不能把有效租约判成
    失约、也不能把失约判成有效。这里**不**用任何时钟判定业务绝对期限——那由 `expire_runs`
    的 PG 时钟扫描负责，超时行会先变成终态，再在这里被回收。
    """
    active = supervisor.active_runs()
    if not active:
        return 0
    async with factory() as session:
        instant = now if now is not None else await database_now(session)
        owned = set(
            await session.scalars(
                select(RunRecord.id).where(
                    RunRecord.id.in_(tuple(active)),
                    RunRecord.status == RunStatus.RUNNING,
                    RunRecord.lease_owner == instance_id,
                    RunRecord.lease_until > instant,
                    RunRecord.is_deleted.is_(False),
                )
            )
        )
    return sum(1 for run_id in active - owned if supervisor.reclaim(run_id))
