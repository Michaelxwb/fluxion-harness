from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import TenantContext
from helpers import RecordingExecutor, create_task_payload, fetch_events, fetch_task, persist_task
from muad_agent_worker.application.task_service import TaskService
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import TaskExecutionError
from muad_agent_worker.worker.service import TASK_DEADLINE_EXCEEDED, WorkerLoop
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

INSTANCE_ID = "test-worker:1"


class _CancellingExecutor:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def execute(self, task: TaskExecution) -> dict[str, object]:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(TaskExecution)
                .where(TaskExecution.id == task.id)
                .values(cancel_requested=True)
            )
        raise TaskExecutionError("STOP", "cancelled while running")


async def test_claim_sets_running_lease_and_completes(tenant: TenantContext) -> None:
    async with tenant.session_factory() as session:
        task = await TaskService(session, tenant.settings).create(
            create_task_payload(tenant, idempotency_key="claim-success")
        )
        await session.commit()
    executor = RecordingExecutor({"ok": True})
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor,
        instance_id=INSTANCE_ID,
    )
    claim_now = datetime.now(UTC)
    completed_id = await loop.run_once(now=claim_now)
    assert completed_id == task.id
    claimed = executor.calls[0]
    assert claimed.status == "RUNNING"
    assert claimed.attempt == 1
    assert claimed.lease_owner == INSTANCE_ID
    assert claimed.lease_until is not None
    assert claimed.lease_until > claim_now
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "COMPLETED"
    assert refreshed.result_json == {"ok": True}
    assert refreshed.finished_at is not None
    assert refreshed.lease_owner is None
    assert refreshed.started_at is not None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CREATED", "CLAIMED", "COMPLETED"]


async def test_claim_respects_not_before_and_cancel_requested(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    blocked = await persist_task(tenant, not_before=now + timedelta(minutes=5))
    cancelled = await persist_task(tenant, cancel_requested=True)
    executor = RecordingExecutor()
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor,
        instance_id=INSTANCE_ID,
    )
    assert await loop.run_once(now=now) is None
    assert executor.calls == []

    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == blocked.id)
            .values(not_before=now - timedelta(seconds=1))
        )
    claimed_id = await loop.run_once(now=now)
    assert claimed_id == blocked.id
    assert executor.calls[0].id == blocked.id
    still_queued = await fetch_task(tenant, cancelled.id)
    assert still_queued.status == "QUEUED"


async def test_failure_retries_with_exponential_backoff_then_fails(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    task = await persist_task(tenant, max_attempts=3, not_before=now)
    executor = RecordingExecutor(error=TaskExecutionError("BOOM", "kaboom"))
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor,
        instance_id=INSTANCE_ID,
    )
    await loop.run_once(now=now)
    first = await fetch_task(tenant, task.id)
    assert first.status == "QUEUED"
    assert first.attempt == 1
    assert first.error_code == "BOOM"
    assert first.error_message == "kaboom"
    assert first.not_before == now + timedelta(seconds=5)

    second_now = first.not_before + timedelta(seconds=1)
    await loop.run_once(now=second_now)
    second = await fetch_task(tenant, task.id)
    assert second.status == "QUEUED"
    assert second.attempt == 2
    assert second.not_before == second_now + timedelta(seconds=10)

    third_now = second.not_before + timedelta(seconds=1)
    await loop.run_once(now=third_now)
    failed = await fetch_task(tenant, task.id)
    assert failed.status == "FAILED"
    assert failed.attempt == 3
    assert failed.error_code == "BOOM"
    assert failed.finished_at == third_now
    assert failed.lease_owner is None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == [
        "CLAIMED",
        "RETRY",
        "CLAIMED",
        "RETRY",
        "CLAIMED",
        "FAILED",
    ]


async def test_failure_after_cancel_request_ends_cancelled(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    task = await persist_task(tenant, max_attempts=3, not_before=now)
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        _CancellingExecutor(tenant.session_factory),
        instance_id=INSTANCE_ID,
    )
    await loop.run_once(now=now)
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "CANCELLED"
    assert refreshed.cancel_requested is True
    assert refreshed.finished_at == now
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CLAIMED", "CANCELLED"]


async def test_reclaim_expired_lease(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    task = await persist_task(
        tenant,
        status="RUNNING",
        attempt=2,
        lease_owner="dead:1",
        lease_until=now - timedelta(seconds=5),
        started_at=now - timedelta(minutes=1),
    )
    await persist_task(
        tenant,
        status="RUNNING",
        attempt=1,
        lease_owner="dead:2",
        lease_until=now - timedelta(seconds=5),
        cancel_requested=True,
    )
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        instance_id=INSTANCE_ID,
    )
    reclaimed = await loop.reclaim_expired(now=now)
    assert reclaimed == 1
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "QUEUED"
    assert refreshed.attempt == 2
    assert refreshed.lease_owner is None
    assert refreshed.lease_until is None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["RECLAIMED"]


async def test_sweep_deadlines_marks_failed(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    expired = await persist_task(tenant, deadline_at=now - timedelta(seconds=1))
    finished = await persist_task(
        tenant,
        status="COMPLETED",
        deadline_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=5),
    )
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        instance_id=INSTANCE_ID,
    )
    swept = await loop.sweep_deadlines(now=now)
    assert swept == 1
    refreshed = await fetch_task(tenant, expired.id)
    assert refreshed.status == "FAILED"
    assert refreshed.error_code == TASK_DEADLINE_EXCEEDED
    assert refreshed.finished_at == now
    events = await fetch_events(tenant, expired.id)
    assert [event.event_type for event in events] == ["DEADLINE_EXCEEDED"]
    untouched = await fetch_task(tenant, finished.id)
    assert untouched.status == "COMPLETED"
    assert await fetch_events(tenant, finished.id) == []
