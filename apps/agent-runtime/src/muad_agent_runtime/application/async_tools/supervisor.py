"""One registration lock seals admission before bounded execution cleanup."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

logger = logging.getLogger(__name__)


class ExecutionSupervisor:
    def __init__(self, *, close_timeout_sec: float = 10) -> None:
        self._lock = asyncio.Lock()
        self._closing = False
        self._tasks: dict[UUID, asyncio.Task[object]] = {}
        self._timeout = close_timeout_sec

    @property
    def execution_count(self) -> int:
        return len(self._tasks)

    async def submit(self, run_id: UUID, execution: Callable[[], Awaitable[object]]) -> bool:
        async with self._lock:
            if self._closing or run_id in self._tasks:
                return False

            async def invoke() -> object:
                return await execution()

            task: asyncio.Task[object] = asyncio.create_task(invoke(), name=f"run:{run_id}")
            self._tasks[run_id] = task
            task.add_done_callback(lambda completed: self._finished(run_id, completed))
            return True

    def _finished(self, run_id: UUID, task: asyncio.Task[object]) -> None:
        self._tasks.pop(run_id, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "supervised_run_failed", extra={"run_id": str(run_id), "error_type": type(error).__name__}
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
