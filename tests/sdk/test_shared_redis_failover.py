"""Destructive failover drill: opt in only against a disposable Redis Cluster."""
import asyncio
import os
import uuid

import httpx
import pytest
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedRedisMeter
from redis.asyncio import Redis, RedisCluster
from redis.cluster import key_slot

from tests.sdk.test_shared_http_transport import http_probe as http_probe
from tests.sdk.test_shared_http_transport import transport

pytestmark = pytest.mark.integration


@pytest.fixture
async def disposable_cluster():
    if os.environ.get("EGRESS_ALLOW_CLUSTER_FAILOVER") != "1":
        pytest.skip("requires an explicitly disposable Redis Cluster")
    url = os.environ.get("MUAD_CLUSTER_REDIS_URL")
    if not url:
        pytest.fail("MUAD_CLUSTER_REDIS_URL is required")
    client = RedisCluster.from_url(url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def cluster_meter(disposable_cluster):
    client = disposable_cluster
    domain = "failover-" + uuid.uuid4().hex
    meter = SharedRedisMeter(client, domain=domain, epoch="epoch")
    await client.hset(meter._key("guard"), mapping={"epoch": "epoch"})
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "route", "target_instance_id": "platform", "route_id": "GET /assets",
        "max_requests": 10, "max_inflight": 2,
    }]})
    await meter.publish(policy)
    await meter.register_execution("execution", max_requests=10)
    try:
        yield client, meter, policy
    finally:
        keys = [key async for key in client.scan_iter(match=meter._key("*"))]
        if keys:
            await client.delete(*keys)


async def owner(client, key):
    slot = key_slot(key.encode())
    slots = await client.cluster_slots()
    entry = next(entry for (start, end), entry in slots.items() if start <= slot <= end)
    return entry


async def promote_replica(client, key, *, takeover=False):
    async with asyncio.timeout(15):
        while not (entry := await owner(client, key))["replicas"]:
            await asyncio.sleep(0.1)
    address = entry["replicas"][0][:2]
    admin = Redis(host=address[0], port=address[1], decode_responses=True)
    try:
        async with asyncio.timeout(15):
            while (await admin.info("replication")).get("master_link_status") != "up":
                await asyncio.sleep(0.1)
        command = ("CLUSTER", "FAILOVER", "TAKEOVER") if takeover else ("CLUSTER", "FAILOVER")
        await admin.execute_command(*command)
        async with asyncio.timeout(15):
            while (await owner(client, key))["primary"][:2] != address:
                await asyncio.sleep(0.1)
    finally:
        await admin.aclose()
    return address


async def acquire(meter, policy, attempt):
    return await meter.admit(policy, target_instance_id="platform", route_id="GET /assets",
                             attempt_id=attempt, lease_seconds=30)


async def test_promotion_blocks_existing_epoch_without_resetting_counts(cluster_meter):
    client, meter, policy = cluster_meter
    pending = await acquire(meter, policy, "before-promotion")
    before = await client.hgetall(meter._key("guard"))
    await promote_replica(client, meter._key("guard"))
    # Controlled promotion keeps the counter, and the externally supplied epoch
    # remains unchanged. The server-generation check must still refuse use.
    assert await client.get(meter._bucket("route", "total")) == "1"
    with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
        await acquire(meter, policy, "after-promotion")
    with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
        await meter.consume(pending)
    with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
        await meter.resume_execution("execution", max_requests=10)
    with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
        await meter.publish(policy)
    assert await client.hgetall(meter._key("guard")) == before
    assert await client.get(meter._bucket("route", "total")) == "1"


async def test_failback_to_same_process_still_blocks_old_generation(cluster_meter):
    client, meter, policy = cluster_meter
    await acquire(meter, policy, "before-two-promotions")
    key = meter._key("guard")
    original = (await owner(client, key))["primary"][:2]
    admin = Redis(host=original[0], port=original[1], decode_responses=True)
    try:
        run_id = (await admin.info("server"))["run_id"]
        await promote_replica(client, key)
        # Forced failback avoids election cooldown; this is deliberately a
        # TAKEOVER drill, not evidence for a normal consensus-based failback.
        await promote_replica(client, key, takeover=True)
        assert (await owner(client, key))["primary"][:2] == original
        assert (await admin.info("server"))["run_id"] == run_id
        with pytest.raises(MeteringUnavailable, match="SERVER_GENERATION"):
            await acquire(meter, policy, "after-failback")
        assert await client.get(meter._bucket("route", "total")) == "1"
    finally:
        await admin.aclose()


async def test_real_cluster_promotion_closes_host_owned_stream(cluster_meter, http_probe):
    _, meter, policy = cluster_meter
    probe, url = http_probe
    sender = transport(meter, timeout=10, interval=0.1)
    async with httpx.AsyncClient(trust_env=False, timeout=None) as client:
        request = client.build_request("GET", url + "/stream")
        with pytest.raises(MeteringUnavailable, match="supervision unavailable"):
            async with sender.stream(client, request, policy=policy, target_instance_id="platform",
                                     route_id="GET /assets") as response:
                chunks = response.aiter_bytes()
                assert await anext(chunks) == b"first\n"
                await promote_replica(cluster_meter[0], meter._key("guard"))
                await anext(chunks)
        assert response.is_closed
        await asyncio.wait_for(probe.closed.wait(), 1)
    assert probe.calls == 1
