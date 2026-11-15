"""独立后台循环的监督（design §4.1 / §4.2）。

调度扫描、deadline、Human 超时、外部结果补扫、投递、保留清理各自是独立 `asyncio` 任务：
一个循环慢或失败不得推迟其它循环。仅"各自起任务"还不够——任务崩溃后只是悄悄消失，
进程仍然活着的卡死更是不可见。本模块用一个**不等待被监督循环**的独立节拍做三件事：

1. **发现退出**：循环任务结束（未因取消）即计数并重启；
2. **发现停顿**：循环每轮调 `record_loop_tick`，超过自身节拍允许的停顿即计数并重启；
3. **有界收尾**：shutdown 先取消监督器，再取消并等待全部循环；等待有界，卡死循环不拖住退出。

重启与停顿都计入 Worker 指标目录（`worker_loop_restart_total` / `worker_loop_stale_total`），
并写结构化日志：dead loop 必须看得见，不能静默。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..metrics import (
    WORKER_LOOP_RESTART_METRIC,
    WORKER_LOOP_STALE_METRIC,
    inc_counter,
    loop_tick_age,
    mark_loop_started,
)

logger = logging.getLogger("muad_agent_worker.worker.supervision")

LoopBody = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class _Supervised:
    name: str
    body: LoopBody
    stale_after_sec: float
    task: asyncio.Task[None] | None = field(default=None)
    restarts: int = 0


class LoopSupervisor:
    """拥有全部独立循环的任务与重启策略；监督节拍本身不调用任何被监督工作。"""

    def __init__(
        self,
        *,
        check_interval_sec: float,
        restart_backoff_sec: float,
        shutdown_timeout_sec: float,
    ) -> None:
        if check_interval_sec <= 0 or restart_backoff_sec <= 0 or shutdown_timeout_sec <= 0:
            raise ValueError("supervision intervals must be positive")
        self._check_interval_sec = check_interval_sec
        self._restart_backoff_sec = restart_backoff_sec
        self._shutdown_timeout_sec = shutdown_timeout_sec
        self._loops: list[_Supervised] = []
        self._supervisor: asyncio.Task[None] | None = None
        self._started = False

    def add(self, name: str, body: LoopBody, *, stale_after_sec: float) -> None:
        """登记一个循环；`stale_after_sec` 由该循环自己的节拍与 `*Settings` 决定。"""
        if self._started:
            raise RuntimeError("loops must be registered before the supervisor starts")
        if stale_after_sec <= 0:
            raise ValueError("stale threshold must be positive")
        if any(loop.name == name for loop in self._loops):
            raise ValueError(f"duplicate supervised loop: {name}")
        self._loops.append(_Supervised(name=name, body=body, stale_after_sec=stale_after_sec))

    def start(self) -> None:
        if self._started:
            raise RuntimeError("the supervisor already started")
        self._started = True
        for loop in self._loops:
            self._spawn(loop)
        self._supervisor = asyncio.create_task(self._watch_forever(), name="worker.supervisor")

    def _spawn(self, loop: _Supervised) -> None:
        mark_loop_started(loop.name)
        loop.task = asyncio.create_task(loop.body(), name=loop.name)

    async def _watch_forever(self) -> None:
        while True:
            await asyncio.sleep(self._check_interval_sec)
            now = time.monotonic()
            for loop in self._loops:
                self._check(loop, now)

    def _check(self, loop: _Supervised, now: float) -> None:
        task = loop.task
        if task is None or task.done():
            if task is not None and task.cancelled():
                return
            self._restart(loop, reason="exited", error=task.exception() if task else None)
            return
        age = loop_tick_age(loop.name)
        if age is not None and age > loop.stale_after_sec:
            logger.error(
                "worker_loop_stale",
                extra={"loop": loop.name, "age_sec": round(age, 3)},
            )
            inc_counter(WORKER_LOOP_STALE_METRIC, 1, {"loop": loop.name})
            task.cancel()
            self._restart(loop, reason="stale", error=None)

    def _restart(
        self,
        loop: _Supervised,
        *,
        reason: str,
        error: BaseException | None,
    ) -> None:
        loop.restarts += 1
        inc_counter(WORKER_LOOP_RESTART_METRIC, 1, {"loop": loop.name})
        logger.error(
            "worker_loop_restarted",
            extra={"loop": loop.name, "reason": reason, "restarts": loop.restarts},
            exc_info=error,
        )
        # 退避期间不再按旧心跳判停顿；重启任务本身也受 shutdown 取消。
        mark_loop_started(loop.name)
        loop.task = asyncio.create_task(
            self._restart_after_backoff(loop), name=f"{loop.name}.restart"
        )

    async def _restart_after_backoff(self, loop: _Supervised) -> None:
        await asyncio.sleep(self._restart_backoff_sec)
        self._spawn(loop)

    async def aclose(self) -> None:
        """取消监督器与全部循环，并在有界时间内等待它们退出。"""
        if self._supervisor is not None:
            self._supervisor.cancel()
            await self._await_bounded([self._supervisor])
            self._supervisor = None
        tasks = [loop.task for loop in self._loops if loop.task is not None]
        for task in tasks:
            task.cancel()
        await self._await_bounded(tasks)

    async def _await_bounded(self, tasks: list[asyncio.Task[None]]) -> None:
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout_sec)
        if pending:
            logger.error(
                "worker_loop_shutdown_timeout",
                extra={"pending": sorted(task.get_name() for task in pending)},
            )
        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                logger.error("worker_loop_shutdown_error", exc_info=error)


__all__ = ["LoopSupervisor"]
