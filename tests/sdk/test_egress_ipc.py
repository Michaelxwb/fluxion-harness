"""Invocation-private IPC against real Redis and Unix sockets."""
import asyncio
import importlib
import json
import uuid

import pytest
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk.shared_redis import SharedRedisMeter
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


@pytest.fixture
async def ipc_meter():
    redis = Redis.from_url(SharedSettings().redis_url, decode_responses=True)
    domain = "ipc-" + uuid.uuid4().hex
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "assets", "target_instance_id": "platform", "route_id": "GET /assets",
        "max_requests": 5, "max_inflight": 1,
    }]})
    await meter.publish(policy)
    await meter.register_execution("execution", max_requests=5)
    await meter.register_invocation("execution", "script", max_requests=2)
    try:
        yield redis, meter, policy
    finally:
        keys = [key async for key in redis.scan_iter(match=meter._key("*"))]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


def broker_api():
    return importlib.import_module("muad_platform_sdk.egress_ipc")


def create_broker(meter, policy, *, deadline_seconds=2):
    api = broker_api()

    def resolve(attempt):
        if attempt.origin != "https://platform.test" or attempt.method != "GET" or attempt.path != "/assets":
            return None
        return api.EgressTarget("platform", "GET /assets")

    return api.InvocationEgressBroker(meter=meter,
                                      invocation=TrustedInvocation("execution", "script", "segment"),
                                      resolve=resolve, deadline_seconds=deadline_seconds, cleanup_seconds=0.5)


async def exchange(path, payload):
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 2))
    finally:
        writer.close()
        await writer.wait_closed()


async def test_private_ipc_binds_identity_and_releases_only_completed_send(ipc_meter):
    redis, meter, policy = ipc_meter
    api = broker_api()
    async with create_broker(meter, policy) as broker:
        client = api.IPCAdmissionClient(broker.socket_path, timeout_seconds=1)
        assert await asyncio.to_thread(client.probe)
        attempt = api.AttemptMetadata(method="GET", origin="https://platform.test", path="/assets")
        lease = await asyncio.to_thread(client.begin, attempt)
        assert await redis.get(meter._bucket("assets", "total")) == "1"
        with pytest.raises(api.IPCAdmissionError, match="INFLIGHT"):
            await asyncio.to_thread(client.begin, attempt)
        await asyncio.to_thread(lease.finish)
        second = await asyncio.to_thread(client.begin, attempt)
        await asyncio.to_thread(second.finish)
        with pytest.raises(api.IPCAdmissionError, match="TOTAL"):
            await asyncio.to_thread(client.begin, attempt)
        assert broker.exhausted.is_set()
        assert broker.exhausted_scope == "invocation"
        key = meter._scope("invocation", "execution", "script")[2]
        assert await redis.hget(key, "used") == "2"
        assert broker.socket_path.stat().st_mode & 0o777 == 0o600
        assert broker.socket_path.parent.stat().st_mode & 0o777 == 0o700


async def test_ipc_rejects_spoofed_identity_credentials_and_unauthorized_targets(ipc_meter):
    redis, meter, policy = ipc_meter
    async with create_broker(meter, policy) as broker:
        base = {"op": "begin", "method": "GET", "origin": "https://platform.test", "path": "/assets"}
        for extra in ({"execution_id": "other"}, {"headers": {"Cookie": "test-private-value"}},
                      {"body": "test-private-value"}):
            result = await exchange(broker.socket_path, {**base, **extra})
            assert result == {"ok": False, "code": "IPC_INVALID_REQUEST"}
            assert "test-private-value" not in json.dumps(result)
        result = await exchange(broker.socket_path, {**base, "origin": "https://other.test"})
        assert result["code"] == "IPC_TARGET_DENIED"
        assert await redis.get(meter._bucket("assets", "total")) == "0"


async def test_disconnect_does_not_pretend_transport_has_closed(ipc_meter):
    redis, meter, policy = ipc_meter
    api = broker_api()
    async with create_broker(meter, policy) as broker:
        client = api.IPCAdmissionClient(broker.socket_path, timeout_seconds=1)
        attempt = api.AttemptMetadata(method="GET", origin="https://platform.test", path="/assets")
        lease = await asyncio.to_thread(client.begin, attempt)
        await asyncio.to_thread(lease.close)
        with pytest.raises(api.IPCAdmissionError, match="INFLIGHT"):
            await asyncio.to_thread(client.begin, attempt)
        assert await redis.zcard(meter._bucket("assets", "leases")) == 2  # sentinel + live lease


async def test_public_total_limit_does_not_mark_script_exhausted(ipc_meter):
    _, meter, policy = ipc_meter
    api = broker_api()
    config = SharedEgressPolicy.model_validate({"revision": 2, "rules": [{
        "id": "assets", "target_instance_id": "platform", "route_id": "GET /assets",
        "max_requests": 1, "max_inflight": 1,
    }]})
    await meter.publish(config)
    async with create_broker(meter, config) as broker:
        client = api.IPCAdmissionClient(broker.socket_path, timeout_seconds=1)
        attempt = api.AttemptMetadata(method="GET", origin="https://platform.test", path="/assets")
        lease = await asyncio.to_thread(client.begin, attempt)
        await asyncio.to_thread(lease.finish)
        with pytest.raises(api.IPCAdmissionError, match="TOTAL") as caught:
            await asyncio.to_thread(client.begin, attempt)
        assert caught.value.scope == "public"
        assert not broker.exhausted.is_set()


async def test_expired_invocation_cannot_reset_deadline_by_opening_new_connection(ipc_meter):
    redis, meter, policy = ipc_meter
    api = broker_api()
    async with create_broker(meter, policy, deadline_seconds=0.1) as broker:
        await asyncio.sleep(0.15)
        client = api.IPCAdmissionClient(broker.socket_path, timeout_seconds=1)
        attempt = api.AttemptMetadata(method="GET", origin="https://platform.test", path="/assets")
        with pytest.raises(api.IPCAdmissionError, match="IPC_EXECUTION_EXPIRED"):
            await asyncio.to_thread(client.begin, attempt)
        assert await redis.get(meter._bucket("assets", "total")) == "0"


async def test_oversize_and_query_bearing_metadata_is_rejected_without_metering(ipc_meter):
    redis, meter, policy = ipc_meter
    async with create_broker(meter, policy) as broker:
        base = {"op": "begin", "method": "GET", "origin": "https://platform.test"}
        for path in ("/assets?credential=test-value", "/" + "x" * 5000):
            result = await exchange(broker.socket_path, {**base, "path": path})
            assert result == {"ok": False, "code": "IPC_INVALID_REQUEST"}
        assert await redis.get(meter._bucket("assets", "total")) == "0"
