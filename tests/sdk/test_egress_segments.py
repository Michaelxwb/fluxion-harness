"""Execution-segment fencing against real Redis; not Runtime/Worker acceptance."""
import asyncio
import importlib

import pytest
from muad_contracts.shared_egress import TrustedInvocation
from muad_platform_sdk.shared_redis import MeteringUnavailable

from tests.sdk.test_shared_http_transport import http_probe as http_probe_fixture
from tests.sdk.test_shared_redis_meter import policy
from tests.sdk.test_shared_redis_meter import shared_meter as shared_meter_fixture

http_probe = http_probe_fixture
shared_meter = shared_meter_fixture
pytestmark = pytest.mark.integration


def api():
    return importlib.import_module("muad_platform_sdk.egress_segments")


async def setup_scope(shared_meter):
    redis, base, _, domain = shared_meter
    config = policy()
    await base.publish(config)
    await base.register_execution("execution", max_requests=50)
    await base.register_invocation("execution", "script", max_requests=50)
    return redis, base, domain, config


def segment(redis, domain, *, fence=1, segment_id="segment-one"):
    module = api()
    grant = module.SegmentGrant("execution", fence, segment_id)
    return module.FencedRedisMeter(redis, domain=domain, epoch="epoch-1", grant=grant)


async def admit(meter, config, attempt="attempt"):
    return await meter.admit(config, target_instance_id="platform", route_id="GET /assets",
                             attempt_id=attempt, lease_seconds=5,
                             invocation=TrustedInvocation("execution", "script", meter.grant.segment_id))


async def test_unactivated_segment_cannot_send_or_create_count(shared_meter):
    redis, base, domain, config = await setup_scope(shared_meter)
    guarded = segment(redis, domain)
    with pytest.raises(MeteringUnavailable):
        await admit(guarded, config)
    assert await redis.get(base._bucket("route", "total")) == "0"


async def test_takeover_requires_stop_confirmation_and_preserves_counts(shared_meter):
    redis, base, domain, config = await setup_scope(shared_meter)
    old, new = segment(redis, domain), segment(redis, domain, fence=2, segment_id="segment-two")
    await old.activate(lease_seconds=2, stop_margin_seconds=.1)
    permit = await admit(old, config)
    with pytest.raises(MeteringUnavailable):
        await new.activate(lease_seconds=2, stop_margin_seconds=.1)
    await old.confirm_stopped()
    await new.activate(lease_seconds=2, stop_margin_seconds=.1)
    with pytest.raises(MeteringUnavailable):
        await admit(old, config, "old-again")
    with pytest.raises(MeteringUnavailable):
        await old.consume(permit)
    with pytest.raises(MeteringUnavailable):
        await new.consume(permit)
    fresh = await admit(new, config, "new-attempt")
    await new.consume(fresh)
    assert await redis.get(base._bucket("route", "total")) == "2"
    with pytest.raises(MeteringUnavailable):
        await old.activate(lease_seconds=2, stop_margin_seconds=.1)


async def test_expired_segment_cannot_renew_or_resurrect(shared_meter):
    redis, _, domain, config = await setup_scope(shared_meter)
    old = segment(redis, domain)
    await old.activate(lease_seconds=.2, stop_margin_seconds=.2)
    permit = await admit(old, config)
    await old.consume(permit)
    await asyncio.sleep(.25)
    with pytest.raises(MeteringUnavailable):
        await old.renew(permit, lease_seconds=5)
    with pytest.raises(MeteringUnavailable):
        await old.activate(lease_seconds=2, stop_margin_seconds=.2)
    new = segment(redis, domain, fence=2, segment_id="segment-two")
    with pytest.raises(MeteringUnavailable):
        await new.activate(lease_seconds=2, stop_margin_seconds=.2)
    await asyncio.sleep(.2)
    await new.activate(lease_seconds=2, stop_margin_seconds=.2)
    # A stopped sender may release its own consumed HTTP occupancy after closure.
    await old.release(permit)


async def test_missing_segment_record_never_reinitializes_old_execution(shared_meter):
    redis, _, domain, config = await setup_scope(shared_meter)
    guarded = segment(redis, domain)
    await guarded.activate(lease_seconds=2, stop_margin_seconds=.1)
    await redis.delete(guarded.segment_key)
    with pytest.raises(MeteringUnavailable):
        await admit(guarded, config)
    with pytest.raises(MeteringUnavailable):
        await guarded.activate(lease_seconds=2, stop_margin_seconds=.1)


async def test_bound_meter_rejects_other_execution_or_segment(shared_meter):
    redis, _, domain, config = await setup_scope(shared_meter)
    guarded = segment(redis, domain)
    await guarded.activate(lease_seconds=2, stop_margin_seconds=.1)
    for identity in (None, TrustedInvocation("other", "script", "segment-one"),
                     TrustedInvocation("execution", "script", "other")):
        with pytest.raises(MeteringUnavailable):
            await guarded.admit(config, target_instance_id="platform", route_id="GET /assets",
                                attempt_id="forged", lease_seconds=5, invocation=identity)


async def test_new_holder_cannot_renew_old_permit_or_reuse_attempt(shared_meter):
    redis, base, domain, config = await setup_scope(shared_meter)
    old, new = segment(redis, domain), segment(redis, domain, fence=2, segment_id="segment-two")
    await old.activate(lease_seconds=2, stop_margin_seconds=.1)
    permit = await admit(old, config)
    await old.consume(permit)
    await old.confirm_stopped()
    await new.activate(lease_seconds=2, stop_margin_seconds=.1)
    with pytest.raises(MeteringUnavailable):
        await new.renew(permit, lease_seconds=5)
    with pytest.raises(MeteringUnavailable):
        await admit(new, config)
    assert await redis.get(base._bucket("route", "total")) == "1"
    await old.release(permit)


async def test_deleting_segment_and_index_marker_does_not_recreate_it(shared_meter):
    redis, base, domain, _ = await setup_scope(shared_meter)
    guarded = segment(redis, domain)
    await guarded.activate(lease_seconds=2, stop_margin_seconds=.1)
    name, _, _ = base._scope("execution", "execution")
    await redis.delete(guarded.segment_key)
    await redis.hdel(base._key("scope-index"), "segment:" + name)
    with pytest.raises(MeteringUnavailable):
        await guarded.activate(lease_seconds=2, stop_margin_seconds=.1)


async def test_active_owner_can_extend_lease_without_resetting_counts(shared_meter):
    redis, base, domain, config = await setup_scope(shared_meter)
    guarded = segment(redis, domain)
    await guarded.activate(lease_seconds=.6, stop_margin_seconds=.1)
    permit = await admit(guarded, config)
    await guarded.consume(permit)
    await asyncio.sleep(.1)
    await guarded.activate(lease_seconds=1, stop_margin_seconds=.1)
    await asyncio.sleep(.6)
    await admit(guarded, config, "after-original-expiry")
    assert await redis.get(base._bucket("route", "total")) == "2"


async def test_clock_rollback_cannot_activate_or_release_segment(shared_meter):
    redis, base, domain, config = await setup_scope(shared_meter)
    guarded = segment(redis, domain)
    await guarded.activate(lease_seconds=2, stop_margin_seconds=.1)
    before = await redis.hgetall(guarded.segment_key)
    await redis.hincrby(base._key("guard"), "last_ms", 10000)
    with pytest.raises(MeteringUnavailable):
        await guarded.activate(lease_seconds=2, stop_margin_seconds=.1)
    with pytest.raises(MeteringUnavailable):
        await guarded.confirm_stopped()
    with pytest.raises(MeteringUnavailable):
        await admit(guarded, config)
    assert await redis.hgetall(guarded.segment_key) == before
    assert await redis.get(base._bucket("route", "total")) == "0"


async def test_http_clients_reject_old_segment_before_physical_send(shared_meter):
    import httpx
    from muad_platform_sdk.shared_host_transport import HostRoute, SharedHostTransport
    from muad_platform_sdk.shared_http_transport import RequestLifetime

    from tests.sdk.test_shared_host_transport import mcp_probe

    redis, base, domain, config = await setup_scope(shared_meter)
    old, new = segment(redis, domain), segment(redis, domain, fence=2, segment_id="segment-two")
    await old.activate(lease_seconds=5, stop_margin_seconds=.1)
    async with mcp_probe() as (calls, origin):
        def transport(guarded):
            identity = TrustedInvocation("execution", "script", guarded.grant.segment_id)
            return SharedHostTransport(
                guarded, public_rules_enabled=config.enabled, invocation=identity,
                resolve=lambda request: HostRoute("platform", "GET /assets")
                if str(request.url) == origin + "/mcp" else None,
                lifetime=RequestLifetime(timeout_seconds=1, cleanup_seconds=.5,
                                         renewal_interval_seconds=.05),
            )
        async with httpx.AsyncClient(transport=transport(old)) as old_client:
            assert (await old_client.post(origin + "/mcp", json={"method": "tools/call"})).status_code == 429
            # HTTP-only segment: its response has reached EOF and is closed.
            await old.confirm_stopped()
            await new.activate(lease_seconds=5, stop_margin_seconds=.1)
            with pytest.raises(MeteringUnavailable):
                await old_client.post(origin + "/mcp", json={"method": "tools/call"})
            async with httpx.AsyncClient(transport=transport(new)) as new_client:
                response = await new_client.post(origin + "/mcp", json={"method": "tools/call"})
                assert response.status_code == 429
        assert calls == ["tools/call", "tools/call"]
    assert await redis.get(base._bucket("route", "total")) == "2"


async def test_segment_expiry_interrupts_http_without_an_inflight_rule(shared_meter, http_probe):
    import httpx
    from muad_platform_sdk.shared_host_transport import HostRoute, SharedHostTransport
    from muad_platform_sdk.shared_http_transport import RequestLifetime

    redis, _, domain, config = await setup_scope(shared_meter)
    guarded = segment(redis, domain)
    await guarded.activate(lease_seconds=.3, stop_margin_seconds=.1)
    probe, origin = http_probe
    transport = SharedHostTransport(
        guarded, public_rules_enabled=config.enabled,
        resolve=lambda request: HostRoute("platform", "GET /assets"),
        invocation=TrustedInvocation("execution", "script", guarded.grant.segment_id),
        lifetime=RequestLifetime(timeout_seconds=2, cleanup_seconds=.5, renewal_interval_seconds=.05),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MeteringUnavailable):
            await asyncio.wait_for(client.get(origin + "/stream"), 1)
        await asyncio.wait_for(probe.closed.wait(), 1)
