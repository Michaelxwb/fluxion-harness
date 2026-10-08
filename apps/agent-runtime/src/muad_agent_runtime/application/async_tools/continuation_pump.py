"""PostgreSQL wakeup facts feed process-owned supervised execution segments."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from ...infrastructure.db import SessionFactory
from ...infrastructure.models.runtime import RunRecord
from .continuation_service import claim_continuation
from .supervisor import ExecutionSupervisor

logger = logging.getLogger(__name__)


class ContinuationPump:
    def __init__(
        self,
        factory: SessionFactory,
        supervisor: ExecutionSupervisor,
        execute: Callable[[RunRecord], Awaitable[None]],
        *,
        instance_id: str,
        lease_sec: int,
        poll_sec: float = 1,
        batch_size: int = 16,
    ) -> None:
        self._factory, self._supervisor, self._execute = factory, supervisor, execute
        self._instance, self._lease, self._poll, self._batch = instance_id, lease_sec, poll_sec, batch_size

    async def _claim(self, excluded: frozenset[UUID]) -> tuple[UUID, Callable[[], Awaitable[object]]] | None:
        run = await claim_continuation(
            self._factory, instance_id=self._instance, lease_sec=self._lease, exclude_runs=excluded
        )
        if run is None:
            return None

        async def execute() -> None:
            await self._execute(run)

        return run.id, execute

    async def run_once(self) -> int:
        started = 0
        for _ in range(self._batch):
            if not await self._supervisor.claim_and_submit(self._claim):
                break
            started += 1
        return started

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("continuation_pump_failed")
            await asyncio.sleep(self._poll)
