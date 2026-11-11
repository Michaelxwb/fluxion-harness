"""Real Run source row + PG identity authority; not full Run executor recovery."""
import asyncio
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.egress_source_lease import read_run_source_lease
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
from muad_platform_sdk.egress_identity_registry import ExecutionRegistration, IdentityRegistry
from muad_platform_sdk.egress_segment_supervisor import SourceSegmentSupervisor
from muad_platform_sdk.egress_segments import FencedRedisMeter
from muad_platform_sdk.egress_source_authority import PGSegmentAuthority, SourceClaim
from muad_platform_sdk.persistent_egress import PersistentEgressScopes
from muad_platform_sdk.shared_redis import MeteringUnavailable

from tests.sdk.test_egress_identity_registry import identity_environment as identity_environment_fixture

identity_environment = identity_environment_fixture
pytestmark = pytest.mark.integration


@pytest.fixture
async def run_source(identity_environment, request):
    sessions, tenant, meter, _, _ = identity_environment
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with sessions.begin() as session:
        now = await session.scalar(sa.select(sa.func.statement_timestamp()))
        session.add(Conversation(id=conversation_id, tenant_id=tenant, user_id=uuid.uuid4(),
                                  agent_id=uuid.uuid4(), status="ACTIVE", last_seq=0))
        await session.flush()
        session.add(RunRecord(id=run_id, tenant_id=tenant, conversation_id=conversation_id,
                              user_id=uuid.uuid4(), agent_id=uuid.uuid4(), status="RUNNING",
                              input_text="test",
                              trace_id=uuid.uuid4().hex, execution_epoch=1, lease_owner="runtime-owner",
                              lease_until=now + timedelta(minutes=5),
                              deadline_at=now + timedelta(minutes=10)))
    registration = ExecutionRegistration.model_validate({
        "tenant_id": tenant, "source_kind": "RUN", "source_id": run_id, "meter_epoch": "epoch",
        "budget": getattr(request, "param", {}),
    })
    scope = await PersistentEgressScopes(IdentityRegistry(sessions), meter).execution(registration)
    try:
        yield sessions, PGSegmentAuthority(sessions, meter_epoch="epoch"), scope, SourceClaim(
            tenant, "RUN", run_id, "runtime-owner", 1,
        )
    finally:
        async with sessions.begin() as session:
            statement = sa.delete(RunRecord).where(RunRecord.id == run_id, RunRecord.tenant_id == tenant)
            await session.execute(statement)
            await session.execute(sa.delete(Conversation).where(Conversation.id == conversation_id))


async def test_run_authority_is_stable_within_source_claim(run_source):
    sessions, authority, scope, claim = run_source
    first = await authority.authorize(scope.id, claim, read_run_source_lease)
    second = await authority.authorize(scope.id, claim, read_run_source_lease)
    assert first.grant == second.grant
    assert first.grant.execution_id == str(scope.id)
    assert first.grant.fence == 1
    assert 0 < first.remaining_seconds() <= 300
    async with sessions() as session:
        statement = sa.text("SELECT current_fence FROM runtime.egress_execution WHERE id=:id")
        fence = await session.scalar(statement, {"id": scope.id})
        assert fence == 1


async def test_pg_source_loss_stops_supervised_work_before_segment_takeover(run_source, identity_environment):
    sessions, authority, scope, claim = run_source
    _, _, base, redis, _ = identity_environment
    bound = await authority.authorize(scope.id, claim, read_run_source_lease)
    domain = base._prefix.split("{")[1].split("}")[0]
    meter = FencedRedisMeter(redis, domain=domain, epoch=base.epoch, grant=bound.grant)
    stopped, closed = asyncio.Event(), asyncio.Event()
    async def close_clients():
        assert stopped.is_set(), "probe work must observe cancellation before closure receipt"
        closed.set()
    supervisor = SourceSegmentSupervisor(
        authority=authority, execution_id=scope.id, claim=claim, reader=read_run_source_lease,
        meter=meter, refresh_seconds=.1, stop_margin_seconds=.3, close_clients=close_clients,
    )
    async def work():
        async with sessions.begin() as session:
            await session.execute(sa.update(RunRecord).where(RunRecord.id == claim.source_id).values(
                cancel_requested=True,
            ))
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    with pytest.raises(MeteringUnavailable, match="source execution lease lost"):
        await asyncio.wait_for(supervisor.run(work), 3)
    assert stopped.is_set() and closed.is_set()


@pytest.mark.parametrize("values", [{"cancel_requested": True}, {"status": "WAITING_INPUT"},
                                     {"execution_epoch": 2}, {"lease_owner": "another-runtime"}])
async def test_run_losing_ownership_cannot_renew_segment_authority(run_source, values):
    sessions, authority, scope, claim = run_source
    await authority.authorize(scope.id, claim, read_run_source_lease)
    async with sessions.begin() as session:
        await session.execute(sa.update(RunRecord).where(RunRecord.id == claim.source_id).values(**values))
    with pytest.raises(MeteringUnavailable):
        await authority.authorize(scope.id, claim, read_run_source_lease)


async def test_pg_time_is_checked_after_waiting_for_identity_lock(run_source):
    sessions, authority, scope, claim = run_source
    async with sessions.begin() as session:
        await session.execute(sa.update(RunRecord).where(RunRecord.id == claim.source_id).values(
            lease_until=sa.func.statement_timestamp() + sa.text("interval '0.5 second'"),
        ))
    read = asyncio.Event()
    async def traced_reader(session, source):
        lease = await read_run_source_lease(session, source)  # Actual locked PG source row.
        read.set()
        return lease
    async with sessions.begin() as blocker:
        await blocker.execute(sa.text("SELECT id FROM runtime.egress_execution WHERE id=:id FOR UPDATE"),
                              {"id": scope.id})
        pending = asyncio.create_task(authority.authorize(scope.id, claim, traced_reader))
        try:
            await asyncio.wait_for(read.wait(), 2)
            await asyncio.sleep(.6)
            await blocker.rollback()
            with pytest.raises(MeteringUnavailable):
                await asyncio.wait_for(pending, 2)
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    async with sessions() as session:
        statement = sa.text("SELECT current_fence FROM runtime.egress_execution WHERE id=:id")
        fence = await session.scalar(statement, {"id": scope.id})
        assert fence == 0
