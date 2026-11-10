"""B-05 composition: real PG identity + real Redis Cluster promotion."""
import importlib

import pytest
import sqlalchemy as sa
from muad_contracts.shared_egress import TrustedInvocation
from muad_platform_sdk.shared_redis import MeteringUnavailable

from tests.sdk.test_egress_identity_registry import identity_environment as identity_environment
from tests.sdk.test_egress_identity_registry import make_registration
from tests.sdk.test_shared_redis_failover import cluster_meter as cluster_meter
from tests.sdk.test_shared_redis_failover import disposable_cluster as disposable_cluster
from tests.sdk.test_shared_redis_failover import promote_replica

pytestmark = pytest.mark.integration


async def test_pg_identity_and_cluster_recovery_never_reinitialize_old_execution(
    identity_environment, cluster_meter,
):
    sessions, tenant, _, _, _ = identity_environment
    cluster, meter, policy = cluster_meter
    api, request = make_registration(tenant)
    binder_api = importlib.import_module("muad_platform_sdk.persistent_egress")
    binder = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    execution = await binder.execution(request)
    invocation = await binder.invocation(tenant_id=tenant, execution_id=execution.id,
                                         operation_key="persisted-tool-call", skill_key="skill")
    identity = TrustedInvocation(str(execution.id), str(invocation.id), "segment-before")
    for attempt in ("one", "two"):
        permit = await meter.admit(policy, target_instance_id="platform", route_id="GET /assets",
                                   attempt_id=attempt, lease_seconds=30, invocation=identity)
        await meter.consume(permit)
        await meter.release(permit)
    async with sessions.begin() as session:
        await session.execute(sa.text("UPDATE runtime.egress_execution SET create_time="
                                      "clock_timestamp()-interval '8 days' WHERE tenant_id=:tenant"),
                              {"tenant": tenant})
    restarted = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    assert (await restarted.execution(request)).id == execution.id
    restored = await restarted.invocation(tenant_id=tenant, execution_id=execution.id,
                                         operation_key="persisted-tool-call", skill_key="skill")
    assert restored.id == invocation.id
    invocation_key = meter._scope("invocation", str(execution.id), str(invocation.id))[2]
    assert await cluster.hget(invocation_key, "used") == "2"
    await promote_replica(cluster, meter._key("guard"))
    with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
        await restarted.execution(request)
    with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
        await restarted.invocation(tenant_id=tenant, execution_id=execution.id,
                                   operation_key="persisted-tool-call", skill_key="skill")
    assert await cluster.hget(invocation_key, "used") == "2"
    assert await cluster.get(meter._bucket("route", "total")) == "2"


async def test_pg_ready_identity_refuses_missing_cluster_scope(identity_environment, cluster_meter):
    sessions, tenant, _, _, _ = identity_environment
    cluster, meter, _ = cluster_meter
    api, request = make_registration(tenant)
    binder_api = importlib.import_module("muad_platform_sdk.persistent_egress")
    binder = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    execution = await binder.execution(request)
    key = meter._scope("execution", str(execution.id))[2]
    await cluster.delete(key)
    with pytest.raises(MeteringUnavailable):
        await binder.execution(request)
    assert not await cluster.exists(key)
