"""Real PG job ownership, savepoint isolation, causal ordering and fixed input."""

import asyncio
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.models.runtime import (
    MemoryConsolidationJob,
    RunRecord,
    RuntimeSnapshot,
)

from tests.agent_runtime.memory_confirmation_support import source
from tests.agent_runtime.memory_consolidation_support import model_stack

pytestmark = pytest.mark.integration


async def test_local_successor_cannot_overtake_running_or_retry_predecessor(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database) as stack:
        factory, run, job, snapshot, *_ = stack
        _, later_run, _, _ = await source(async_tool_database, tenant=run.tenant_id, user=run.user_id)
        async with factory.begin() as session:
            later = await session.get(RunRecord, later_run.id)
            later.status, later.conversation_id = "COMPLETED", run.conversation_id
            later_snapshot = await session.get(RuntimeSnapshot, later.snapshot_id)
            later_snapshot.policy_json = snapshot.policy_json
            first = await session.get(MemoryConsolidationJob, job.id)
            first.status, first.attempt, first.lease_until = "PENDING", 0, None
            session.add(
                MemoryConsolidationJob(
                    tenant_id=run.tenant_id,
                    run_id=later.id,
                    conversation_id=run.conversation_id,
                    user_id=run.user_id,
                    snapshot_id=later.snapshot_id,
                    through_seq=2,
                    enqueued_scope_revision=0,
                )
            )
        owners = jobs.MemoryJobs(factory)
        first_claim = await owners.claim("first")
        assert first_claim.id == job.id and await owners.claim("blocked") is None
        assert await owners.set_failure(first_claim, reason="FAILED") is True
        assert await owners.claim("still_blocked") is None
        async with factory.begin() as session:
            first = await session.get(MemoryConsolidationJob, job.id)
            first.status = "FAILED"
        next_claim = await owners.claim("successor")
        assert next_claim is not None and next_claim.run_id == later_run.id


@pytest.mark.parametrize("status", ["RUNNING", "WAITING_INPUT", "WAITING_TOOL", "FAILED", "CANCELLED"])
async def test_local_noncompleted_run_never_enqueues(async_tool_database, status):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database) as stack:
        factory, run, job, *_ = stack
        async with factory.begin() as session:
            await session.delete(await session.get(MemoryConsolidationJob, job.id))
            row = await session.get(RunRecord, run.id)
            row.status = status
            assert await jobs.enqueue_completed(session, row, through_seq=1) is False
        async with factory() as session:
            assert await session.scalar(sa.select(sa.func.count()).select_from(MemoryConsolidationJob)) == 0


async def test_local_claim_is_unique_and_old_epoch_cannot_write(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database) as stack:
        factory, _, job, _, *_ = stack
        async with factory.begin() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            row.status, row.lease_owner, row.lease_until = "PENDING", None, None
            row.attempt = 0
        owners = jobs.MemoryJobs(factory)
        results = await asyncio.gather(owners.claim("one"), owners.claim("two"))
        claim = next(item for item in results if item is not None)
        assert sum(item is not None for item in results) == 1
        async with factory.begin() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        second = await owners.claim("takeover")
        assert second.epoch == claim.epoch + 1 and second.owner == "takeover"
        assert await owners.set_failure(claim, reason="FAILED") is False
        async with factory() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            assert row.status == "RUNNING" and row.lease_owner == "takeover"


async def test_local_enqueue_is_unique_and_database_error_keeps_completed_run(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database) as stack:
        factory, run, job, snapshot, *_ = stack
        async with factory.begin() as session:
            await session.delete(await session.get(MemoryConsolidationJob, job.id))
            row = await session.get(RunRecord, run.id)
            assert await jobs.enqueue_completed(session, row, through_seq=1) is True
            assert await jobs.enqueue_completed(session, row, through_seq=1) is False
        async with factory.begin() as session:
            await session.execute(sa.delete(MemoryConsolidationJob))
            await session.execute(
                sa.text(
                    "CREATE FUNCTION runtime.reject_job() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RAISE EXCEPTION 'injected job failure'; END $$"
                )
            )
            await session.execute(
                sa.text(
                    "CREATE TRIGGER reject_job BEFORE INSERT ON runtime.memory_consolidation_job "
                    "FOR EACH ROW EXECUTE FUNCTION runtime.reject_job()"
                )
            )
        async with factory.begin() as session:
            row = await session.get(RunRecord, run.id)
            assert await jobs.enqueue_completed(session, row, through_seq=1) is False
            row.status = "COMPLETED"
        async with factory() as session:
            assert (await session.get(RunRecord, run.id)).status == "COMPLETED"
            assert await session.scalar(sa.select(sa.func.count()).select_from(MemoryConsolidationJob)) == 0


async def test_local_fixed_input_survives_takeover_without_following_tail(async_tool_database):
    jobs = import_module("muad_agent_runtime.application.memory_jobs")
    async with model_stack(async_tool_database) as stack:
        factory, run, job, snapshot, *_ = stack
        async with factory.begin() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            row.status, row.lease_owner, row.lease_until, row.attempt = "PENDING", None, None, 0
        owners = jobs.MemoryJobs(factory)
        first = await owners.claim("one")
        payload = await owners.freeze_input(first, credential_canaries=())
        async with factory.begin() as session:
            before = await session.get(MemoryConsolidationJob, job.id)
            initial_hash = before.input_hash
            before.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="USER_MESSAGE",
                payload={"text": "new tail must not enter R1"},
            )
        second = await owners.claim("two")
        assert await owners.freeze_input(second, credential_canaries=()) == payload
        assert await owners.freeze_input(first, credential_canaries=()) is None
        async with factory() as session:
            row = await session.get(MemoryConsolidationJob, job.id)
            assert row.input_hash == initial_hash and row.input_scope_revision == 0
            assert "new tail" not in str(row.input_json)
