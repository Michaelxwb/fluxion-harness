"""Real Task PG ownership -> SDK segment authority -> shared Redis counters.

This does not invoke SkillTaskExecutor or a Linux script process; S-03 remains
owned by its formal Runtime/Worker recovery acceptance entry point.
"""
import importlib
import uuid

import pytest
import sqlalchemy as sa
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk.egress_identity_registry import ExecutionRegistration, IdentityRegistry
from muad_platform_sdk.persistent_egress import PersistentEgressScopes
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded, SharedRedisMeter
from redis.asyncio import Redis

from agent_worker.helpers import persist_task
from tests.agent_worker.test_task_lease_epoch import waiting_again


@pytest.fixture
async def authority_environment(tenant):
    redis = Redis.from_url(tenant.settings.redis_url, decode_responses=True)
    domain = "authority-" + uuid.uuid4().hex
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "route", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 100,
    }]})
    await meter.publish(policy)
    scopes = PersistentEgressScopes(IdentityRegistry(tenant.session_factory), meter)
    task = await persist_task(tenant)
    claimed = await TaskClaimer(tenant.session_factory, tenant.settings).claim_one("worker")
    assert claimed.id == task.id
    request = ExecutionRegistration.model_validate({
        "tenant_id": tenant.tenant_id, "source_kind": "TASK", "source_id": task.id,
        "meter_epoch": "epoch", "budget": {"execution": {"max_requests": 50},
                                            "invocations": {"skill": {"max_requests": 50}}},
    })
    execution = await scopes.execution(request)
    invocation = await scopes.invocation(tenant_id=tenant.tenant_id, execution_id=execution.id,
                                         operation_key="skill:0", skill_key="skill")
    try:
        yield redis, domain, policy, claimed, execution, invocation, scopes, request
    finally:
        async with tenant.session_factory.begin() as session:
            for table in ("egress_invocation", "egress_execution"):
                await session.execute(sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id=:tenant"),
                                      {"tenant": tenant.tenant_id})
        keys = [key async for key in redis.scan_iter(meter._key("*"))]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


def authority(tenant):
    api = importlib.import_module("muad_platform_sdk.egress_source_authority")
    reader = importlib.import_module("muad_agent_worker.worker.egress_source_lease").read_task_source_lease
    return api, api.PGSegmentAuthority(tenant.session_factory, meter_epoch="epoch"), reader


def claim(api, task):
    return api.SourceClaim(task.tenant_id, "TASK", task.id, task.lease_owner, task.lease_epoch)


async def test_task_authority_rejects_stale_claim_and_preserves_30_of_50(tenant, authority_environment):
    redis, domain, policy, task, execution, invocation, scopes, request = authority_environment
    api, registrar, reader = authority(tenant)
    first = await registrar.authorize(execution.id, claim(api, task), reader)
    assert first.grant.fence == task.lease_epoch
    from muad_platform_sdk.egress_segments import FencedRedisMeter
    old = FencedRedisMeter(redis, domain=domain, epoch="epoch", grant=first.grant)
    await old.activate(lease_seconds=first.remaining_seconds(), stop_margin_seconds=.1)
    identity = TrustedInvocation(str(execution.id), str(invocation.id), first.grant.segment_id)
    for index in range(30):
        permit = await old.admit(policy, target_instance_id="platform", route_id="GET /assets",
                                 attempt_id=f"first-{index}", lease_seconds=2, invocation=identity)
        await old.consume(permit)
        await old.release(permit)
    await old.confirm_stopped()  # no child or outstanding HTTP exists in this SDK-only test
    await waiting_again(tenant, task)
    second_task = await TaskClaimer(tenant.session_factory, tenant.settings).claim_one("worker")
    with pytest.raises(MeteringUnavailable):
        await registrar.authorize(execution.id, claim(api, task), reader)
    recovered = await scopes.execution(request)
    recovered_invocation = await scopes.invocation(tenant_id=tenant.tenant_id, execution_id=recovered.id,
                                                   operation_key="skill:0", skill_key="skill")
    assert (recovered.id, recovered_invocation.id) == (execution.id, invocation.id)
    second = await registrar.authorize(recovered.id, claim(api, second_task), reader)
    assert second.grant.fence > first.grant.fence
    new = FencedRedisMeter(redis, domain=domain, epoch="epoch", grant=second.grant)
    await new.activate(lease_seconds=second.remaining_seconds(), stop_margin_seconds=.1)
    for index in range(21):
        kwargs = dict(target_instance_id="platform", route_id="GET /assets", attempt_id=f"second-{index}",
                      lease_seconds=2, invocation=identity.with_segment(second.grant.segment_id))
        if index < 20:
            permit = await new.admit(policy, **kwargs)
            await new.consume(permit)
            await new.release(permit)
        else:
            with pytest.raises(SharedLimitExceeded):
                await new.admit(policy, **kwargs)


@pytest.mark.parametrize("change", [
    "tenant", "owner", "epoch", "source", "kind", "cancel", "expired", "deadline", "status", "deleted",
])
async def test_invalid_task_source_never_gets_a_segment(tenant, authority_environment, change):
    _, _, _, task, execution, _, _, _ = authority_environment
    api, registrar, reader = authority(tenant)
    value = claim(api, task)
    if change in {"tenant", "owner", "epoch", "source", "kind"}:
        from dataclasses import replace
        field, content = {"tenant": ("tenant_id", "other"), "owner": ("owner", "other"),
                          "epoch": ("epoch", task.lease_epoch + 1), "source": ("source_id", uuid.uuid4()),
                          "kind": ("source_kind", "RUN")}[change]
        value = replace(value, **{field: content})
    else:
        values = {"cancel": {"cancel_requested": True}, "status": {"status": "WAITING"},
                  "deleted": {"is_deleted": True},
                  "expired": {"lease_until": sa.func.now() - sa.text("interval '1 second'")},
                  "deadline": {"deadline_at": sa.func.now() - sa.text("interval '1 second'")}}[change]
        async with tenant.session_factory.begin() as session:
            statement = sa.update(TaskExecution).where(TaskExecution.id == task.id).values(**values)
            await session.execute(statement)
    with pytest.raises(MeteringUnavailable):
        await registrar.authorize(execution.id, value, reader)
    async with tenant.session_factory() as session:
        statement = sa.text("SELECT current_fence FROM runtime.egress_execution WHERE id=:id")
        current = await session.scalar(statement, {"id": execution.id})
        assert current == 0
