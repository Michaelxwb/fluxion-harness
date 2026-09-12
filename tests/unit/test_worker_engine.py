import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from framework.domain.execution import ExecutionStatus, ServiceExecution
from framework.execution.worker_engine import StepOutcome, WorkerEngine


class FakeExecutionRepository:
    def __init__(self) -> None:
        self.claims: list[int] = []
        self.terminal: list[tuple[str, ExecutionStatus]] = []
        self.waiting: list[str] = []
        self._to_claim: list[ServiceExecution] = []

    def queue(self, execution: ServiceExecution) -> None:
        self._to_claim.append(execution)

    async def find_by_idempotency_key(self, key: str) -> ServiceExecution | None:
        return None

    async def create(self, execution: ServiceExecution, snapshot: object) -> None:
        raise AssertionError("not used in engine loop tests")

    async def get(self, execution_id: UUID) -> ServiceExecution | None:
        return None

    async def heartbeat(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> None:
        raise AssertionError("not used in engine loop tests")

    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[ServiceExecution]:
        self.claims.append(lease_seconds)
        claimed, self._to_claim = self._to_claim[:limit], self._to_claim[limit:]
        return claimed

    async def mark_terminal(
        self, execution_id: UUID, *, worker_id: str, status: ExecutionStatus
    ) -> None:
        self.terminal.append((str(execution_id), status))

    async def mark_waiting(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        next_run_at: datetime,
        status: ExecutionStatus | None = None,
    ) -> None:
        self.waiting.append(str(execution_id))


def _execution() -> ServiceExecution:
    return ServiceExecution(
        id=uuid4(),
        actor_user_id=uuid4(),
        service_id=uuid4(),
        service_release_id=uuid4(),
        idempotency_key=f"idem-{uuid4().hex[:8]}",
    )


def handler_script(
    outcomes: list[StepOutcome],
) -> Callable[[ServiceExecution], Awaitable[StepOutcome]]:
    remaining = list(outcomes)

    async def _handler(execution: ServiceExecution) -> StepOutcome:
        return remaining.pop(0)

    return _handler


async def failing_handler(execution: ServiceExecution) -> StepOutcome:
    raise RuntimeError("boom")


async def _run_once(engine: WorkerEngine) -> None:
    engine_task = asyncio.create_task(engine.run_forever())
    await asyncio.sleep(0.05)
    engine.stop()
    await asyncio.wait_for(engine_task, timeout=2.0)


async def test_engine_marks_terminal_success() -> None:
    repo = FakeExecutionRepository()
    execution = _execution()
    repo.queue(execution)
    engine = WorkerEngine(repo, handler_script([StepOutcome(terminal=ExecutionStatus.SUCCEEDED)]))
    await _run_once(engine)
    assert repo.terminal == [(str(execution.id), ExecutionStatus.SUCCEEDED)]
    assert repo.waiting == []


async def test_engine_schedules_retry_wait() -> None:
    repo = FakeExecutionRepository()
    execution = _execution()
    repo.queue(execution)
    engine = WorkerEngine(repo, handler_script([StepOutcome(wait_seconds=30.0)]))
    await _run_once(engine)
    assert repo.waiting == [str(execution.id)]
    assert repo.terminal == []


async def test_engine_handler_exception_fails_execution() -> None:
    repo = FakeExecutionRepository()
    execution = _execution()
    repo.queue(execution)
    engine = WorkerEngine(repo, failing_handler)
    await _run_once(engine)
    assert repo.terminal == [(str(execution.id), ExecutionStatus.FAILED)]


async def test_engine_idles_without_claims() -> None:
    repo = FakeExecutionRepository()
    engine = WorkerEngine(repo, handler_script([]), poll_seconds=0.01)
    await _run_once(engine)
    assert repo.terminal == []


def test_step_outcome_requires_exactly_one_decision() -> None:
    with pytest.raises(ValueError):
        StepOutcome()
    with pytest.raises(ValueError):
        StepOutcome(terminal=ExecutionStatus.FAILED, wait_seconds=1.0)


def test_lease_and_wait_use_tz_aware_datetimes() -> None:
    future = datetime.now(UTC) + timedelta(seconds=1)
    assert future.tzinfo is not None
