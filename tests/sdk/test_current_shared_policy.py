"""Real Redis and physical HTTP/IPC: current public policy, frozen scope counts."""
import asyncio

import httpx
import pytest
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_platform_sdk.egress_ipc import AttemptMetadata, IPCAdmissionClient, IPCAdmissionError
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded

from tests.sdk.test_egress_ipc import create_broker
from tests.sdk.test_egress_ipc import ipc_meter as ipc_meter_fixture
from tests.sdk.test_shared_host_transport import shared_transport
from tests.sdk.test_shared_http_transport import http_probe as http_probe_fixture
from tests.sdk.test_shared_http_transport import meter as meter_fixture

ipc_meter = ipc_meter_fixture
http_probe = http_probe_fixture
meter = meter_fixture
pytestmark = pytest.mark.integration


async def test_existing_host_transport_uses_current_public_revision(meter, http_probe):
    counter, original, _ = meter
    probe, origin = http_probe
    transport = shared_transport(counter, original, origin)
    changed = SharedEgressPolicy(revision=original.revision + 1, rules=(
        original.rules[0].model_copy(update={"max_requests": 1}),
    ))
    await counter.publish(changed)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(origin + "/ok")
        assert response.status_code == 429 and response.headers["retry-after"] == "9"
        assert response.content == b"business"
        with pytest.raises(SharedLimitExceeded) as error:
            await client.get(origin + "/ok")
        assert error.value.scope == "public"
    assert probe.calls == 1


async def test_existing_broker_reloads_public_rules_without_resetting_frozen_scope(ipc_meter):
    redis, meter, original = ipc_meter
    async with create_broker(meter, original) as broker:
        client = IPCAdmissionClient(broker.socket_path, timeout_seconds=1)
        metadata = AttemptMetadata(origin="https://platform.test", method="GET", path="/assets")
        first = await asyncio.to_thread(client.begin, metadata)
        await asyncio.to_thread(first.finish)
        changed = original.model_copy(update={"revision": 2, "rules": (
            original.rules[0].model_copy(update={"max_requests": 4}),
        )})
        await meter.publish(changed)
        second = await asyncio.to_thread(client.begin, metadata)
        await asyncio.to_thread(second.finish)
        with pytest.raises(IPCAdmissionError, match="TOTAL"):
            await asyncio.to_thread(client.begin, metadata)
        assert broker.exhausted_scope == "invocation"
        assert await redis.hget(meter._scope("invocation", "execution", "script")[2], "used") == "2"


async def test_current_policy_reader_rejects_corrupt_revision_without_writes(meter):
    counter, original, redis = meter
    await redis.hset(counter._key("policy"), "revision", str(original.revision + 1))
    before = await redis.hgetall(counter._key("policy"))
    with pytest.raises(MeteringUnavailable):
        await counter.current_policy()
    assert await redis.hgetall(counter._key("policy")) == before


async def test_policy_read_wait_is_inside_host_request_deadline(meter, http_probe, monkeypatch):
    counter, original, _ = meter
    probe, origin = http_probe
    async def stalled():
        await asyncio.Event().wait()
    monkeypatch.setattr(counter, "current_policy", stalled)
    transport = shared_transport(counter, original, origin, timeout=.1)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MeteringUnavailable):
            await asyncio.wait_for(client.get(origin + "/ok"), .5)
    assert probe.calls == 0


async def test_publication_between_read_and_admission_does_not_retry_or_send(meter, http_probe, monkeypatch):
    counter, original, redis = meter
    probe, origin = http_probe
    read = counter.current_policy
    async def race():
        current = await read()
        await counter.publish(current.model_copy(update={"revision": current.revision + 1}))
        return current
    monkeypatch.setattr(counter, "current_policy", race)
    async with httpx.AsyncClient(transport=shared_transport(counter, original, origin)) as client:
        with pytest.raises(MeteringUnavailable):
            await client.get(origin + "/ok")
    assert probe.calls == 0
    assert await redis.get(counter._bucket("stream", "total")) == "0"


@pytest.mark.parametrize("damage", ["missing_guard", "generation", "clock", "json", "policy_type"])
async def test_policy_reader_fails_closed_without_repairing_state(meter, damage):
    counter, _, redis = meter
    guard, policy = counter._key("guard"), counter._key("policy")
    if damage == "missing_guard":
        await redis.delete(guard)
    elif damage == "generation":
        await redis.hset(guard, "server_generation", "unknown")
    elif damage == "clock":
        await redis.hset(guard, "last_ms", "9007199254740990")
    elif damage == "json":
        await redis.hset(policy, "json", "invalid")
    else:
        await redis.delete(policy)
        await redis.set(policy, "wrong-type")
    before = [await redis.dump(key) for key in (guard, policy)]
    with pytest.raises(MeteringUnavailable):
        await counter.current_policy()
    assert [await redis.dump(key) for key in (guard, policy)] == before
