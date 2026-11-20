"""执行期的停止检查：cancel 标记、租约归属与绝对期限。

Kernel 的 `is_cancelled` 是**同步**回调（每个模型/工具回合都会读一次），所以探针在后台按节拍
读 PG，把结论写进 `asyncio.Event`：同步侧只读内存标志，不阻塞循环，也不在紧循环里打库
（Forbidden Patterns：tight loop 里的网络调用）。

它只**停止本地执行**：写终态仍由 WorkerLoop 的 CAS 收口（失约时那里会拒绝写）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from muad_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.models.task import TaskExecution

logger = get_logger("muad_agent_worker.application.agentic.cancellation")

DEFAULT_POLL_INTERVAL_SEC = 2.0


class CancellationProbe:
    """后台轮询执行行；命中取消/失约/过期就把本地执行标记为停止。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        tenant_id: str,
        execution_id: UUID,
        owner: str,
        epoch: int,
        deadline_at: datetime,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    ) -> None:
        self._factory = factory
        self._tenant_id = tenant_id
        self._execution_id = execution_id
        self._owner = owner
        self._epoch = epoch
        self._deadline_at = deadline_at
        self._poll_interval_sec = poll_interval_sec
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._reason: str | None = None

    @property
    def reason(self) -> str | None:
        return self._reason

    def is_cancelled(self) -> bool:
        return self._stop.is_set()

    async def __aenter__(self) -> CancellationProbe:
        # 先同步读一次：执行开始时就已经被取消/失约的，不该再跑一个轮次的模型调用。
        if await self._should_stop():
            self._stop.set()
            return self
        self._task = asyncio.create_task(self._poll(), name="agentic-cancel-probe")
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._poll_interval_sec)
            if await self._should_stop():
                self._stop.set()
                return

    async def _should_stop(self) -> bool:
        if datetime.now(UTC) >= self._deadline_at:
            self._reason = "DEADLINE"
            return True
        try:
            async with self._factory() as session:
                row = (
                    await session.execute(
                        select(
                            TaskExecution.cancel_requested,
                            TaskExecution.status,
                            TaskExecution.lease_owner,
                            TaskExecution.lease_epoch,
                        ).where(
                            TaskExecution.id == self._execution_id,
                            TaskExecution.tenant_id == self._tenant_id,
                            TaskExecution.is_deleted.is_(False),
                        )
                    )
                ).one_or_none()
        except Exception as exc:  # noqa: BLE001 —— 读不到就不停止本地执行，但要留痕
            logger.warning(
                "agentic_cancel_probe_failed",
                extra={"execution_id": str(self._execution_id), "error_type": type(exc).__name__},
            )
            return False
        if row is None:
            self._reason = "MISSING"
            return True
        cancel_requested, status, owner, epoch = row
        if cancel_requested or status != "RUNNING" or owner != self._owner or epoch != self._epoch:
            self._reason = "CANCELLED"
            return True
        return False


__all__ = ["CancellationProbe"]
