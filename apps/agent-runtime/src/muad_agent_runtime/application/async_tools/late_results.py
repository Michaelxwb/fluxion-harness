"""Bounded late-result materializer: terminal Runs can still gain model-visible facts."""

from __future__ import annotations

import asyncio

from muad_logging import get_logger

from ...infrastructure.db import SessionFactory
from ...metrics import RUNTIME_MAINTENANCE_METRIC, record_outcome
from .materialization import materialize_late_results

logger = get_logger(__name__)


class LateBackgroundResultPump:
    def __init__(
        self,
        factory: SessionFactory,
        *,
        poll_sec: float = 1.0,
        batch_size: int = 16,
    ) -> None:
        if not 0 < poll_sec <= 60:
            raise ValueError("late result poll interval must be in (0, 60]")
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 100:
            raise ValueError("late result batch size must be in 1..100")
        self._factory = factory
        self._poll = poll_sec
        self._batch = batch_size

    async def run_once(self) -> int:
        return await materialize_late_results(self._factory, batch_limit=self._batch)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "failed", {"loop": "late-results"})
                logger.exception("late_background_result_pump_failed")
            else:
                record_outcome(RUNTIME_MAINTENANCE_METRIC, "ok", {"loop": "late-results"})
            await asyncio.sleep(self._poll)
