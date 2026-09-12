"""Durable Worker loop (module 06).

Claim/lease/retry/recovery live in the execution repository (PostgreSQL
``SELECT ... FOR UPDATE SKIP LOCKED``); this engine only drives the loop and
delegates each claimed execution to an injected step handler. Resource
governance defaults (RULE-WORK-06) are constructor parameters so tests and
deployments can tune them without code changes.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from framework.domain.execution import ExecutionStatus, ServiceExecution
from framework.execution.repository import ExecutionRepository

logger = logging.getLogger("worker.engine")

DEFAULT_LEASE_SECONDS = 300
DEFAULT_POLL_SECONDS = 1.0


class StepOutcome:
    """What a step handler decided; exactly one of the three fields is set."""

    def __init__(
        self,
        *,
        terminal: ExecutionStatus | None = None,
        wait_seconds: float | None = None,
        failed: bool = False,
    ) -> None:
        count = sum(1 for value in (terminal, wait_seconds, failed) if value not in (None, False))
        if count != 1:
            raise ValueError("StepOutcome must set exactly one outcome")
        self.terminal = terminal
        self.wait_seconds = wait_seconds
        self.failed = failed


StepHandler = Callable[[ServiceExecution], Awaitable[StepOutcome]]


class WorkerEngine:
    def __init__(
        self,
        repository: ExecutionRepository,
        step_handler: StepHandler,
        *,
        worker_id: str = "worker-1",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        claim_limit: int = 1,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self.repository = repository
        self.step_handler = step_handler
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.claim_limit = claim_limit
        self.poll_seconds = poll_seconds
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        logger.info("worker_started", extra={"event": "worker_started", "worker_id": self.worker_id})
        while not self._stopped.is_set():
            claimed = await self.repository.claim_due(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                limit=self.claim_limit,
            )
            for execution in claimed:
                await self._run_claimed(execution)
            if not claimed:
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    continue

    async def _run_claimed(self, execution: ServiceExecution) -> None:
        execution_id: UUID = execution.id
        try:
            outcome = await self.step_handler(execution)
        except Exception:
            logger.exception(
                "step_handler_error",
                extra={"event": "step_handler_error", "execution_id": str(execution_id)},
            )
            outcome = StepOutcome(terminal=ExecutionStatus.FAILED)
        if outcome.terminal is not None:
            await self.repository.mark_terminal(
                execution_id, worker_id=self.worker_id, status=outcome.terminal
            )
        elif outcome.failed:
            await self.repository.mark_terminal(
                execution_id, worker_id=self.worker_id, status=ExecutionStatus.FAILED
            )
        elif outcome.wait_seconds is not None:
            await self.repository.mark_waiting(
                execution_id,
                worker_id=self.worker_id,
                next_run_at=_now_plus(outcome.wait_seconds),
            )


def _now_plus(seconds: float) -> datetime:
    from datetime import UTC, timedelta

    return datetime.now(UTC) + timedelta(seconds=seconds)
