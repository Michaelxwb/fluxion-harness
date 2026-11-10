"""Real HTTP sockets and isolated Redis for host-owned streaming lifecycle."""
import asyncio
import importlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded, SharedRedisMeter
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

pytestmark = pytest.mark.integration


@dataclass
class HTTPProbe:
    calls: int = 0
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    writers: list[asyncio.StreamWriter] = field(default_factory=list)
    headers: list[bytes] = field(default_factory=list)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            self.calls += 1
            self.headers.append(headers)
            path = headers.split(b" ")[1]
            if path == b"/stream":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\nfirst\n")
                await writer.drain()
                await reader.read()
                self.closed.set()
            elif path == b"/redirect":
                writer.write(b"HTTP/1.1 302 Found\r\nLocation: /proxy\r\n"
                             b"Content-Length: 0\r\n\r\n")
                await writer.drain()
            else:
                writer.write(b"HTTP/1.1 429 Too Many Requests\r\nRetry-After: 9\r\n"
                             b"Content-Length: 8\r\n\r\nbusiness")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.fixture
async def http_probe():
    probe = HTTPProbe()
    server = await asyncio.start_server(probe.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield probe, f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()
        for writer in probe.writers:
            writer.close()
            await writer.wait_closed()


@pytest.fixture
async def meter():
    url = SharedSettings().redis_url
    if not url:
        pytest.fail("REDIS_URL must point to isolated Redis")
    redis = Redis.from_url(url, decode_responses=True)
    domain = "stream-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    await redis.hset(prefix + "guard", mapping={"epoch": "epoch"})
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    policy = SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
        "id": "stream", "target_instance_id": "platform", "route_id": "GET /stream",
        "max_inflight": 1,
    }]})
    await meter.publish(policy)
    try:
        yield meter, policy, redis
    finally:
        keys = [key async for key in redis.scan_iter(prefix + "*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


def transport(meter, *, timeout=1, interval=0.05):
    module = importlib.import_module("muad_platform_sdk.shared_http_transport")
    lifetime = module.RequestLifetime(timeout_seconds=timeout, cleanup_seconds=0.5,
                                      renewal_interval_seconds=interval)
    return module.SharedHTTPTransport(meter, lifetime=lifetime)


@asynccontextmanager
async def open_stream(sender, client, url, policy):
    async with sender.stream(client, client.build_request("GET", url), policy=policy,
                             target_instance_id="platform", route_id="GET /stream") as response:
        yield response


async def test_stream_holds_lease_until_close_and_preserves_proxy_response(meter, http_probe):
    counter, policy, redis = meter
    probe, url = http_probe
    sender = transport(counter)
    async with httpx.AsyncClient(trust_env=False) as client:
        async with open_stream(sender, client, url + "/stream", policy) as response:
            chunks = response.aiter_bytes()
            assert await anext(chunks) == b"first\n"
            with pytest.raises(SharedLimitExceeded):
                async with open_stream(sender, client, url + "/stream", policy):
                    pytest.fail("second concurrent send was allowed")
            assert probe.calls == 1
        await asyncio.wait_for(probe.closed.wait(), 1)
        assert await redis.zcard(counter._bucket("stream", "leases")) == 1  # sentinel only
        async with open_stream(sender, client, url + "/proxy", policy) as response:
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "9"
            assert await response.aread() == b"business"
    assert probe.calls == 2


async def test_deadline_covers_body_read_and_closes_real_socket(meter, http_probe):
    counter, policy, redis = meter
    probe, url = http_probe
    sender = transport(counter, timeout=0.25)
    async with httpx.AsyncClient(trust_env=False, timeout=None) as client:
        with pytest.raises(TimeoutError):
            async with open_stream(sender, client, url + "/stream", policy) as response:
                seconds, micros = await redis.time()
                entries = await redis.zrange(counter._bucket("stream", "leases"), 0, -1, withscores=True)
                expiry = max(score for _, score in entries)
                # Occupancy outlives the entire remaining host deadline and cleanup margin.
                assert expiry - (seconds * 1000 + micros // 1000) > 490
                await response.aread()
        assert response.is_closed
        await asyncio.wait_for(probe.closed.wait(), 1)
    assert await redis.zcard(counter._bucket("stream", "leases")) == 1


async def test_renewal_epoch_failure_closes_stream_as_dependency_error(meter, http_probe):
    counter, policy, redis = meter
    probe, url = http_probe
    sender = transport(counter)
    async with httpx.AsyncClient(trust_env=False, timeout=None) as client:
        with pytest.raises(MeteringUnavailable):
            async with open_stream(sender, client, url + "/stream", policy) as response:
                chunks = response.aiter_bytes()
                assert await anext(chunks) == b"first\n"
                await redis.hset(counter._key("guard"), "epoch", "new-epoch")
                await anext(chunks)
        assert response.is_closed
        await asyncio.wait_for(probe.closed.wait(), 1)
    assert probe.calls == 1


async def test_external_cancellation_closes_stream_and_keeps_cancelled_error(meter, http_probe):
    counter, policy, redis = meter
    probe, url = http_probe
    reading = asyncio.Event()
    sender = transport(counter)

    async def read():
        async with httpx.AsyncClient(trust_env=False) as client:
            async with open_stream(sender, client, url + "/stream", policy) as response:
                chunks = response.aiter_bytes()
                assert await anext(chunks) == b"first\n"
                reading.set()
                await anext(chunks)

    task = asyncio.create_task(read())
    await asyncio.wait_for(reading.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(probe.closed.wait(), 1)
    assert await redis.zcard(counter._bucket("stream", "leases")) == 1


class RedisFaultProxy:
    """Forward real Redis commands but optionally discard server replies."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.drop_replies = False
        self.tasks = set()

    def accept(self, reader, writer):
        task = asyncio.create_task(self.bridge(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def pump(self, reader, writer, *, replies=False):
        while data := await reader.read(65536):
            if not (replies and self.drop_replies):
                writer.write(data)
                await writer.drain()

    async def bridge(self, reader, writer):
        upstream_reader, upstream_writer = await asyncio.open_connection(self.host, self.port)
        sending = asyncio.create_task(self.pump(reader, upstream_writer))
        receiving = asyncio.create_task(self.pump(upstream_reader, writer, replies=True))
        try:
            await asyncio.wait((sending, receiving), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sending, receiving):
                task.cancel()
            await asyncio.gather(sending, receiving, return_exceptions=True)
            upstream_writer.close()
            writer.close()
            await upstream_writer.wait_closed()
            await writer.wait_closed()


@pytest.fixture
async def fault_proxy(meter):
    _, _, redis = meter
    connection = redis.connection_pool.connection_kwargs
    proxy = RedisFaultProxy(connection["host"], connection["port"])
    server = await asyncio.start_server(proxy.accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = Redis(host="127.0.0.1", port=port, db=connection.get("db", 0),
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


async def test_lost_redis_reply_never_sends_and_same_attempt_retry_does_not_recount(
    meter, http_probe, fault_proxy,
):
    counter, _, redis = meter
    probe, url = http_probe
    proxy, client = fault_proxy
    config = SharedEgressPolicy.model_validate({"revision": 2, "rules": [{
        "id": "stream", "target_instance_id": "platform", "route_id": "GET /stream",
        "max_requests": 5, "max_inflight": 1,
    }]})
    await counter.publish(config)
    via_proxy = SharedRedisMeter(client, domain=counter._prefix.split("{")[1].split("}")[0],
                                 epoch="epoch")
    args = dict(target_instance_id="platform", route_id="GET /stream", lease_seconds=2,
                attempt_id="lost-reply")
    proxy.drop_replies = True
    with pytest.raises(MeteringUnavailable):
        await via_proxy.admit(config, **args)
    assert await redis.get(counter._bucket("stream", "total")) == "1"
    proxy.drop_replies = False
    permit = await via_proxy.admit(config, **args)
    assert await redis.get(counter._bucket("stream", "total")) == "1"
    await via_proxy.release(permit)
    # Warm the connection before dropping the next EVAL reply.
    await client.ping()
    proxy.drop_replies = True
    async with httpx.AsyncClient(trust_env=False) as http:
        with pytest.raises(MeteringUnavailable):
            async with open_stream(transport(via_proxy), http, url + "/stream", config):
                pytest.fail("uncertain Redis admission reached HTTP")
    assert probe.calls == 0
    assert await redis.get(counter._bucket("stream", "total")) == "2"


async def test_admission_deadline_is_dependency_failure_not_http_timeout(meter, http_probe, fault_proxy):
    counter, policy, _ = meter
    probe, url = http_probe
    proxy, client = fault_proxy
    # A dedicated slow-timeout client lets the host deadline expire inside Redis.
    slow = Redis(host="127.0.0.1", port=client.connection_pool.connection_kwargs["port"],
                 socket_timeout=5, retry=Retry(NoBackoff(), 0), decode_responses=True)
    via_proxy = SharedRedisMeter(slow, domain=counter._prefix.split("{")[1].split("}")[0],
                                 epoch="epoch")
    try:
        await slow.ping()
        proxy.drop_replies = True
        async with httpx.AsyncClient(trust_env=False) as http:
            with pytest.raises(MeteringUnavailable, match="admission deadline"):
                async with open_stream(transport(via_proxy, timeout=0.25), http,
                                       url + "/stream", policy):
                    pytest.fail("uncertain admission reached HTTP")
        assert probe.calls == 0
    finally:
        await slow.aclose()


async def test_renewal_network_timeout_closes_active_stream(meter, http_probe, fault_proxy):
    counter, policy, _ = meter
    probe, url = http_probe
    proxy, client = fault_proxy
    via_proxy = SharedRedisMeter(client, domain=counter._prefix.split("{")[1].split("}")[0],
                                 epoch="epoch")
    async with httpx.AsyncClient(trust_env=False, timeout=None) as http:
        with pytest.raises(MeteringUnavailable, match="supervision unavailable"):
            async with open_stream(transport(via_proxy), http, url + "/stream", policy) as response:
                chunks = response.aiter_bytes()
                assert await anext(chunks) == b"first\n"
                proxy.drop_replies = True
                await anext(chunks)
        assert response.is_closed
        await asyncio.wait_for(probe.closed.wait(), 1)
    assert probe.calls == 1


async def test_redirect_is_returned_without_an_unmetered_followup(meter, http_probe):
    counter, policy, _ = meter
    probe, url = http_probe
    async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
        async with open_stream(transport(counter), client, url + "/redirect", policy) as response:
            assert response.status_code == 302
            assert response.headers["Location"] == "/proxy"
    assert probe.calls == 1


async def test_no_rules_stream_does_not_access_unavailable_redis(meter, http_probe, fault_proxy):
    counter, _, _ = meter
    probe, url = http_probe
    proxy, client = fault_proxy
    via_proxy = SharedRedisMeter(client, domain=counter._prefix.split("{")[1].split("}")[0],
                                 epoch="unprovisioned-epoch")
    policy = SharedEgressPolicy(revision=1, rules=())
    proxy.drop_replies = True
    async with httpx.AsyncClient(trust_env=False) as http:
        async with open_stream(transport(via_proxy), http, url + "/proxy", policy) as response:
            assert await response.aread() == b"business"
    assert probe.calls == 1


async def test_release_failure_keeps_received_proxy_result_and_logs(meter, http_probe, fault_proxy, caplog):
    counter, policy, _ = meter
    probe, url = http_probe
    proxy, client = fault_proxy
    via_proxy = SharedRedisMeter(client, domain=counter._prefix.split("{")[1].split("}")[0],
                                 epoch="epoch")
    async with httpx.AsyncClient(trust_env=False) as http:
        async with open_stream(transport(via_proxy, interval=0.5), http, url + "/proxy", policy) as response:
            assert await response.aread() == b"business"
            proxy.drop_replies = True
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "9"
        assert response.content == b"business"
    assert "shared_egress_lease_release_failed" in caplog.text
    assert probe.calls == 1


async def test_prepared_authentication_headers_are_preserved(meter, http_probe):
    counter, policy, _ = meter
    probe, url = http_probe
    sender = transport(counter)
    headers = {"X-CSRFToken": "fixed-test-csrf", "Cookie": "test-session=value"}
    async with httpx.AsyncClient(trust_env=False) as client:
        request = client.build_request("GET", url + "/proxy", headers=headers)
        async with sender.stream(client, request, policy=policy, target_instance_id="platform",
                                 route_id="GET /stream") as response:
            assert await response.aread() == b"business"
    assert b"X-CSRFToken: fixed-test-csrf\r\n" in probe.headers[0]
    assert b"Cookie: test-session=value\r\n" in probe.headers[0]
