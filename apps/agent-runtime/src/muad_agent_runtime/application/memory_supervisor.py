"""Bounded process tasks consume PostgreSQL leases and close before clients."""

import asyncio
import logging
from functools import partial
from uuid import UUID

from pydantic import JsonValue

from ..metrics import RUNTIME_MAINTENANCE_METRIC, record_outcome
from .memory_confirmation_expiry import expire_confirmations
from .memory_consolidation_effects import ConsolidationWorker
from .memory_expiry import expire_memories
from .memory_observability import observe

logger = logging.getLogger(__name__)


class MemorySupervisor:
    def __init__(
        self,
        worker: ConsolidationWorker,
        *,
        instance_id: str,
        max_concurrency: int = 4,
        poll_sec: float = 1,
        close_timeout_sec: float = 10,
    ) -> None:
        if not instance_id or len(instance_id) > 128:
            raise ValueError("invalid memory supervisor identity")
        if isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= 16:
            raise ValueError("invalid memory concurrency")
        if poll_sec <= 0 or close_timeout_sec <= 0:
            raise ValueError("invalid memory supervisor interval")
        self._worker, self._instance, self._limit = worker, instance_id, max_concurrency
        self._poll, self._close_timeout = poll_sec, close_timeout_sec
        self._tasks: dict[UUID, asyncio.Task[dict[str, JsonValue] | None]] = {}
        self._lock, self._closing = asyncio.Lock(), False

    @property
    def execution_count(self) -> int:
        return len(self._tasks)

    async def run_once(self) -> int:
        count = 0
        async with self._lock:
            while not self._closing and len(self._tasks) < self._limit:
                claim = await self._worker.jobs.claim(self._instance, exclude=frozenset(self._tasks))
                if claim is None:
                    break
                task = asyncio.create_task(self._worker.execute(claim), name=f"memory-job:{claim.id}")
                self._tasks[claim.id] = task
                task.add_done_callback(partial(self._finished, claim.id))
                count += 1
        return count

    def _finished(self, identity: UUID, task: asyncio.Task[dict[str, JsonValue] | None]) -> None:
        self._tasks.pop(identity, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning(
                "supervised_memory_job_failed",
                extra={"job_id": str(identity), "error_type": type(error).__name__},
            )
            observe("memory_consolidation_total", labels={"outcome": "ERROR"})

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
                await expire_memories(self._worker.factory)
                await expire_confirmations(self._worker.factory)
            except Exception as exc:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "memory"})
                logger.warning("memory_background_pump_failed", extra={"error_type": type(exc).__name__})
                observe("memory_consolidation_total", labels={"outcome": "ERROR"})
            else:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "memory"})
            await asyncio.sleep(self._poll)

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            tasks = tuple(self._tasks.values())
            for task in tasks:
                task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._close_timeout)
            if pending:
                logger.error("memory_supervisor_close_timeout", extra={"pending_count": len(pending)})
                raise TimeoutError("memory executions did not stop within close budget")
        self._tasks.clear()
