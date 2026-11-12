"""PG authority and real Redis initialization/recovery protocol."""
import asyncio
import importlib
import uuid

import pytest
import sqlalchemy as sa
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded, SharedRedisMeter
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity_environment():
    settings = SharedSettings()
    engine = create_async_engine(settings.require_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tenant = "identity-" + uuid.uuid4().hex
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    domain = "identity-" + uuid.uuid4().hex
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "route", "target_instance_id": "platform", "route_id": "GET /assets", "max_requests": 100,
    }]})
    await meter.publish(policy)
    try:
        yield sessions, tenant, meter, redis, policy
    finally:
        async with engine.begin() as connection:
            for table in ("egress_invocation", "egress_execution"):
                await connection.execute(sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id=:tenant"),
                                         {"tenant": tenant})
        keys = [key async for key in redis.scan_iter(match=meter._key("*"))]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()


def make_registration(tenant, *, source=None, limit=5):
    api = importlib.import_module("muad_platform_sdk.egress_identity_registry")
    request = api.ExecutionRegistration.model_validate({
        "tenant_id": tenant, "source_kind": "TASK", "source_id": source or uuid.uuid4(),
        "meter_epoch": "epoch", "budget": {
            "execution": {"max_requests": limit}, "invocations": {"skill": {"max_requests": 3}},
        },
    })
    return api, request


async def test_concurrent_registration_reuses_identity_and_rejects_changed_budget(identity_environment):
    sessions, tenant, _, _, _ = identity_environment
    api, request = make_registration(tenant)
    registry = api.IdentityRegistry(sessions)
    identities = await asyncio.gather(*(registry.register_execution(request) for _ in range(8)))
    assert len({row.id for row in identities}) == 1
    changed = request.model_copy(update={"budget": request.budget.model_copy(
        update={"execution": request.budget.execution.model_copy(update={"max_requests": 6})},
    )})
    with pytest.raises(MeteringUnavailable):
        await registry.register_execution(changed)


async def test_initialization_has_one_owner_and_uncertain_state_cannot_be_retried(identity_environment):
    sessions, tenant, _, _, _ = identity_environment
    api, request = make_registration(tenant)
    registry = api.IdentityRegistry(sessions)
    scope = await registry.register_execution(request)
    claim = await registry.claim_initialization(scope)
    assert claim is not None
    assert claim.token not in repr(claim)
    async with sessions() as session:
        stored = await session.scalar(sa.text("SELECT init_token_hash FROM runtime.egress_execution "
                                              "WHERE tenant_id=:tenant AND id=:id"),
                                      {"tenant": tenant, "id": scope.id})
        assert stored != claim.token
    with pytest.raises(MeteringUnavailable):
        await registry.claim_initialization(scope)
    assert await registry.block_initialization(claim)
    with pytest.raises(MeteringUnavailable):
        await registry.register_execution(request)


async def test_recovery_reuses_ids_and_remaining_budget(identity_environment):
    sessions, tenant, meter, _, policy = identity_environment
    api, request = make_registration(tenant)
    binder_api = importlib.import_module("muad_platform_sdk.persistent_egress")
    binder = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    execution = await binder.execution(request)
    invocation = await binder.invocation(tenant_id=tenant, execution_id=execution.id,
                                         operation_key="durable-call", skill_key="skill")
    identity = TrustedInvocation(str(execution.id), str(invocation.id), "segment-1")
    args = dict(target_instance_id="platform", route_id="GET /assets", lease_seconds=30)
    for attempt in ("first", "second"):
        assert await meter.admit(policy, attempt_id=attempt, invocation=identity, **args)
    restarted = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    assert (await restarted.execution(request)).id == execution.id
    assert (await restarted.invocation(tenant_id=tenant, execution_id=execution.id,
                                      operation_key="durable-call", skill_key="skill")).id == invocation.id
    assert await meter.admit(policy, attempt_id="third",
                             invocation=identity.with_segment("segment-2"), **args)
    with pytest.raises(SharedLimitExceeded):
        await meter.admit(policy, attempt_id="fourth", invocation=identity, **args)


async def test_ready_pg_identity_never_recreates_missing_redis_scope(identity_environment):
    sessions, tenant, meter, redis, _ = identity_environment
    api, request = make_registration(tenant)
    binder_api = importlib.import_module("muad_platform_sdk.persistent_egress")
    binder = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    execution = await binder.execution(request)
    key = meter._scope("execution", str(execution.id))[2]
    await redis.delete(key)
    with pytest.raises(MeteringUnavailable):
        await binder.execution(request)
    assert not await redis.exists(key)


async def test_invocation_retry_is_stable_and_cannot_cross_tenants(identity_environment):
    sessions, tenant, meter, _, _ = identity_environment
    api, request = make_registration(tenant)
    binder_api = importlib.import_module("muad_platform_sdk.persistent_egress")
    binder = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    execution = await binder.execution(request)
    first = await binder.invocation(tenant_id=tenant, execution_id=execution.id,
                                    operation_key="call-a", skill_key="skill")
    same = await binder.invocation(tenant_id=tenant, execution_id=execution.id,
                                   operation_key="call-a", skill_key="skill")
    other = await binder.invocation(tenant_id=tenant, execution_id=execution.id,
                                    operation_key="call-b", skill_key="skill")
    assert first.id == same.id and first.id != other.id
    with pytest.raises(MeteringUnavailable):
        await binder.invocation(tenant_id="another-tenant", execution_id=execution.id,
                                operation_key="call-a", skill_key="skill")
    with pytest.raises(MeteringUnavailable):
        await binder.invocation(tenant_id=tenant, execution_id=execution.id,
                                operation_key="call-a", skill_key="unknown")


async def test_confirmation_cannot_be_forged_or_replayed(identity_environment):
    sessions, tenant, _, _, _ = identity_environment
    api, request = make_registration(tenant)
    registry = api.IdentityRegistry(sessions)
    scope = await registry.register_execution(request)
    claim = await registry.claim_initialization(scope)
    forged = api.InitializationClaim(scope, "wrong-token")
    with pytest.raises(MeteringUnavailable):
        await registry.confirm_initialization(forged)
    await registry.confirm_initialization(claim)
    with pytest.raises(MeteringUnavailable):
        await registry.confirm_initialization(claim)
    assert not await registry.block_initialization(claim)


@pytest.fixture
async def reply_loss_proxy(identity_environment):
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff

    from tests.sdk.test_shared_http_transport import RedisFaultProxy

    connection = identity_environment[3].connection_pool.connection_kwargs
    proxy = RedisFaultProxy(connection["host"], connection["port"])
    server = await asyncio.start_server(proxy.accept, "127.0.0.1", 0)
    client = Redis(host="127.0.0.1", port=server.sockets[0].getsockname()[1],
                   decode_responses=True, socket_timeout=0.1, retry=Retry(NoBackoff(), 0))
    await client.ping()
    try:
        yield proxy, client
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()
        tasks = list(proxy.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_lost_initialization_reply_blocks_pg_and_never_reinitializes(
    identity_environment, reply_loss_proxy,
):
    sessions, tenant, meter, redis, _ = identity_environment
    api, request = make_registration(tenant)
    binder_api = importlib.import_module("muad_platform_sdk.persistent_egress")
    registry = api.IdentityRegistry(sessions)
    scope = await registry.register_execution(request)
    proxy, client = reply_loss_proxy
    domain = meter._prefix.split("{")[1].split("}")[0]
    proxied = SharedRedisMeter(client, domain=domain, epoch="epoch")
    binder = binder_api.PersistentEgressScopes(registry, proxied)
    proxy.drop_replies = True
    with pytest.raises(MeteringUnavailable):
        await binder.execution(request)
    key = meter._scope("execution", str(scope.id))[2]
    assert await redis.exists(key)  # Redis really committed before its reply was lost.
    async with sessions() as session:
        state = await session.scalar(sa.text("SELECT state FROM runtime.egress_execution "
                                             "WHERE tenant_id=:tenant AND id=:id"),
                                     {"tenant": tenant, "id": scope.id})
        assert state == "BLOCKED"
    proxy.drop_replies = False
    await redis.delete(key)
    with pytest.raises(MeteringUnavailable):
        await binder.execution(request)
    assert not await redis.exists(key)


async def test_changed_fence_prevents_old_initializer_confirmation(identity_environment):
    sessions, tenant, _, _, _ = identity_environment
    api, request = make_registration(tenant)
    registry = api.IdentityRegistry(sessions)
    scope = await registry.register_execution(request)
    claim = await registry.claim_initialization(scope)
    async with sessions.begin() as session:
        await session.execute(sa.text("UPDATE runtime.egress_execution SET current_fence=current_fence+1 "
                                      "WHERE tenant_id=:tenant AND id=:id"),
                              {"tenant": tenant, "id": scope.id})
    with pytest.raises(MeteringUnavailable):
        await registry.confirm_initialization(claim)
    assert await registry.block_initialization(claim)


async def test_tombstone_and_terminal_rows_cannot_be_registered_again(identity_environment):
    sessions, tenant, _, _, _ = identity_environment
    api, request = make_registration(tenant)
    registry = api.IdentityRegistry(sessions)
    scope = await registry.register_execution(request)
    async with sessions.begin() as session:
        await session.execute(sa.text("UPDATE runtime.egress_execution SET is_deleted=true "
                                      "WHERE tenant_id=:tenant AND id=:id"),
                              {"tenant": tenant, "id": scope.id})
    with pytest.raises(MeteringUnavailable):
        await registry.register_execution(request)
    async with sessions.begin() as session:
        await session.execute(sa.text("UPDATE runtime.egress_execution SET is_deleted=false,state='TERMINAL' "
                                      "WHERE tenant_id=:tenant AND id=:id"),
                              {"tenant": tenant, "id": scope.id})
    with pytest.raises(MeteringUnavailable):
        await registry.register_execution(request)


async def test_unconfigured_skill_has_no_implicit_invocation_cap(identity_environment):
    sessions, tenant, meter, _, _ = identity_environment
    api, request = make_registration(tenant)
    binder_api = importlib.import_module("muad_platform_sdk.persistent_egress")
    binder = binder_api.PersistentEgressScopes(api.IdentityRegistry(sessions), meter)
    execution = await binder.execution(request)
    invocation = await binder.invocation(tenant_id=tenant, execution_id=execution.id,
                                         operation_key="other-skill", skill_key="unconfigured")
    assert invocation.budget.max_requests is None
    assert invocation.budget.targets == ()
