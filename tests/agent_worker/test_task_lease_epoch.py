"""Real PG regression: a reused worker name must not revive an older claim."""
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.service import WorkerLoop

from agent_worker.conftest import TenantContext
from agent_worker.helpers import persist_task


async def waiting_again(tenant: TenantContext, task: TaskExecution) -> None:
    async with tenant.session_factory.begin() as session:
        await session.execute(sa.update(TaskExecution).where(TaskExecution.id == task.id).values(
            status="WAITING", lease_owner=None, lease_until=None,
            not_before=datetime.now(UTC) - timedelta(seconds=1),
        ))


async def test_stale_claim_cannot_complete_after_same_worker_reclaims_waiting(tenant):
    original = await persist_task(tenant, status="RUNNING", lease_owner="same-worker",
                                  lease_until=datetime.now(UTC) + timedelta(minutes=5), attempt=1)
    await waiting_again(tenant, original)
    current = await TaskClaimer(tenant.session_factory, tenant.settings).claim_one("same-worker")
    assert current is not None and current.attempt == original.attempt
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="same-worker")
    await worker._handle_success(original, {"stale": True}, now=None)
    async with tenant.session_factory() as session:
        stored = await session.get(TaskExecution, current.id)
        assert stored.status == "RUNNING"
        assert stored.result_json is None
    await worker._handle_success(current, {"fresh": True}, now=None)
    async with tenant.session_factory() as session:
        stored = await session.get(TaskExecution, current.id)
        assert stored.status == "COMPLETED"
        assert stored.result_json == {"fresh": True}


async def test_every_claim_advances_epoch_without_spending_wait_retry(tenant):
    original = await persist_task(tenant)
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    first = await claimer.claim_one("same-worker")
    assert first.id == original.id
    assert first.lease_epoch == 1 and first.attempt == 1
    await waiting_again(tenant, first)
    second = await claimer.claim_one("same-worker")
    assert second.lease_epoch == 2 and second.attempt == 1


async def test_stale_epoch_cannot_renew_or_transition_waiting(tenant):
    original = await persist_task(tenant)
    claimer = TaskClaimer(tenant.session_factory, tenant.settings)
    first = await claimer.claim_one("same-worker")
    await waiting_again(tenant, first)
    current = await claimer.claim_one("same-worker")
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="same-worker")
    owned, _ = await worker._renew_lease(current.id, lease_epoch=first.lease_epoch)
    assert owned is False
    async with tenant.session_factory.begin() as session:
        changed = await worker._cas(session, current.id, {"status": "WAITING"},
                                    moment=await worker._pg_clock(), lease_epoch=first.lease_epoch)
        assert changed == 0
    assert (await worker._renew_lease(current.id, lease_epoch=current.lease_epoch))[0] is True
    async with tenant.session_factory() as session:
        stored = await session.get(TaskExecution, current.id)
        assert stored.status == "RUNNING"
        assert stored.lease_epoch == current.lease_epoch
        assert stored.id == original.id
