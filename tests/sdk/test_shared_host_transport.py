"""Real Adapter/MCP + HTTP sockets + isolated Redis shared-send boundaries."""

import asyncio
import importlib
import json
import ssl
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
    McpToolError,
)
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk import MSSAdapter, PlatformRequest, PlatformTarget
from muad_platform_sdk.platform_transport import PlatformHttpSender
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded

from tests.sdk.test_mss_sessions import platform_server as platform_server_fixture
from tests.sdk.test_platform_budget_http import record_requests
from tests.sdk.test_shared_http_transport import http_probe as http_probe_fixture
from tests.sdk.test_shared_http_transport import meter as meter_fixture

platform_server = platform_server_fixture
http_probe = http_probe_fixture
meter = meter_fixture
pytestmark = pytest.mark.integration


def shared_transport(
    counter, policy, origin, *, verify=True, timeout=2, max_bytes=1024 * 1024, invocation=None, client=None
):
    module = importlib.import_module("muad_platform_sdk.shared_host_transport")
    from muad_platform_sdk.shared_http_transport import RequestLifetime

    def resolve(request):
        if str(request.url.copy_with(path="/", query=None)) != origin + "/":
            return None
        return module.HostRoute("platform", "GET /stream")

    return module.SharedHostTransport(
        counter,
        public_rules_enabled=policy.enabled,
        resolve=resolve,
        max_response_bytes=max_bytes,
        verify=verify,
        invocation=invocation,
        client=client,
        lifetime=RequestLifetime(timeout_seconds=timeout, cleanup_seconds=0.5, renewal_interval_seconds=0.05),
    )


async def test_scoped_transport_does_not_close_borrowed_process_pool(meter, http_probe):
    counter, policy, redis = meter
    probe, origin = http_probe
    async with httpx.AsyncClient(trust_env=False) as pool:
        async with httpx.AsyncClient(
            transport=shared_transport(counter, policy, origin, client=pool)
        ) as scoped:
            assert (await scoped.get(origin + "/proxy")).status_code == 429
        assert not pool.is_closed
        assert (await pool.get(origin + "/proxy")).status_code == 429
    assert probe.calls == 2
    assert await redis.get(counter._bucket("stream", "total")) == "1"


async def policy_limit(counter, limit):
    policy = SharedEgressPolicy.model_validate(
        {
            "revision": 2,
            "rules": [
                {
                    "id": "stream",
                    "target_instance_id": "platform",
                    "route_id": "GET /stream",
                    "max_requests": limit,
                    "max_inflight": 1,
                }
            ],
        }
    )
    await counter.publish(policy)
    return policy


async def test_s_05_mss_auth_validate_business_and_retry_count_every_send(meter, platform_server):
    counter, _, redis = meter
    server, platform, credential = platform_server
    origin = platform.resolver_config["base_url"]
    policy = await policy_limit(counter, 8)
    record_requests(server)
    adapter = MSSAdapter(
        transport_factory=lambda context: shared_transport(counter, policy, origin, verify=context)
    )
    state = await adapter.authenticate(platform, credential)
    assert await adapter.validate(platform, state)
    request = PlatformRequest(target=PlatformTarget(method="GET", path="/events"))
    prepared = await adapter.prepare_request(platform, state, request, None)
    async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
        sender = PlatformHttpSender(platform, client)
        assert (await sender.send(prepared)).status_code == 200
        server.sessions.clear()
        assert (await sender.send(prepared)).status_code == 401
        state = await adapter.authenticate(platform, credential)
        prepared = await adapter.prepare_request(platform, state, request, None)
        assert (await sender.send(prepared)).status_code == 200
        with pytest.raises(SharedLimitExceeded):
            await sender.send(prepared)
    assert len(server.requests) == 8
    assert await redis.get(counter._bucket("stream", "total")) == "8"


@asynccontextmanager
async def mcp_probe():
    calls = []

    async def handle(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            payload = json.loads(await reader.readexactly(length))
            calls.append(payload["method"])
            if payload["method"] == "initialize":
                body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {}}).encode()
                status, extra = b"200 OK", b"Content-Type: application/json\r\n"
            elif payload["method"] == "notifications/initialized":
                body, status, extra = b"", b"202 Accepted", b""
            else:
                body, status = b'{"error":"proxy business"}', b"429 Too Many Requests"
                extra = b"Retry-After: 7\r\n"
            writer.write(
                b"HTTP/1.1 "
                + status
                + b"\r\n"
                + extra
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield calls, f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()


async def test_s_05_mcp_initialize_notification_tool_share_counter(meter):
    counter, _, redis = meter
    policy = await policy_limit(counter, 3)
    async with mcp_probe() as (calls, origin):
        async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
            adapter = McpRuntimeAdapter(audit_writer=None, client=client)
            definition = McpServerDefinition(
                uuid.uuid4(), "knowledge", origin + "/mcp", tools=[McpToolDefinition("search", "", {})]
            )
            try:
                with pytest.raises(Exception) as failure:
                    await adapter.execute_tool(
                        server=definition, tool=definition.tools[0], arguments={}, policy_decision="ALLOW"
                    )
                assert failure.value.reason == "upstream_http"
                assert failure.value.status_code == 429
                assert failure.value.retry_after == "7"
                with pytest.raises(McpToolError) as local_limit:
                    await adapter.execute_tool(
                        server=definition, tool=definition.tools[0], arguments={}, policy_decision="ALLOW"
                    )
                assert local_limit.value.code == "LOCAL_BUDGET_EXHAUSTED"
            finally:
                await adapter.aclose()
        assert calls == ["initialize", "notifications/initialized", "tools/call"]
    assert await redis.get(counter._bucket("stream", "total")) == "3"


async def test_e_03_proxy_response_preserved_and_sent_attempt_not_refunded(meter, http_probe):
    counter, _, redis = meter
    probe, origin = http_probe
    policy = await policy_limit(counter, 1)
    async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
        response = await client.get(origin + "/proxy")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "9"
        assert response.content == b"business"
        assert response.extensions["egress_source"] == "UPSTREAM"
        with pytest.raises(SharedLimitExceeded):
            await client.get(origin + "/proxy")
    assert probe.calls == 1
    assert await redis.get(counter._bucket("stream", "total")) == "1"


async def test_redirect_each_hop_counts_and_unauthorized_target_never_sends(meter, http_probe):
    counter, _, redis = meter
    probe, origin = http_probe
    policy = await policy_limit(counter, 2)
    module = importlib.import_module("muad_platform_sdk.shared_host_transport")
    async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
        response = await client.get(origin + "/redirect", follow_redirects=True)
        assert response.status_code == 429
        assert len(response.history) == 1
        with pytest.raises(module.HostTargetDenied):
            await client.get("http://127.0.0.1:1/not-authorized")
    assert probe.calls == 2
    assert await redis.get(counter._bucket("stream", "total")) == "2"


async def test_streaming_close_releases_concurrency_and_client_close_cancels_pump(meter, http_probe):
    counter, policy, redis = meter
    probe, origin = http_probe
    transport = shared_transport(counter, policy, origin)
    async with httpx.AsyncClient(transport=transport) as client:
        async with client.stream("GET", origin + "/stream") as response:
            assert await anext(response.aiter_bytes()) == b"first\n"
            with pytest.raises(SharedLimitExceeded):
                await client.get(origin + "/proxy")
            assert probe.calls == 1
        await asyncio.wait_for(probe.closed.wait(), 1)
        assert await redis.zcard(counter._bucket("stream", "leases")) == 1
        response = await client.send(client.build_request("GET", origin + "/stream"), stream=True)
        await anext(response.aiter_bytes())
    assert not transport._relays
    assert await redis.zcard(counter._bucket("stream", "leases")) == 1


async def test_no_rules_never_uses_missing_redis_guard(meter, http_probe):
    counter, _, redis = meter
    probe, origin = http_probe
    await redis.delete(counter._key("guard"))
    policy = SharedEgressPolicy(revision=2, rules=[])
    async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
        assert (await client.get(origin + "/proxy")).content == b"business"
    assert probe.calls == 1


async def test_raw_compressed_proxy_body_is_decoded_once(meter):
    import gzip

    counter, _, _ = meter
    policy = await policy_limit(counter, 1)
    body = gzip.compress(b'{"error":"business"}')

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 429 Too Many Requests\r\nContent-Encoding: gzip\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
            response = await client.get(origin + "/proxy")
            assert response.headers["Content-Encoding"] == "gzip"
            assert response.content == b'{"error":"business"}'
    finally:
        server.close()
        await server.wait_closed()


async def test_streaming_deadline_closes_physical_connection(meter, http_probe):
    counter, policy, redis = meter
    probe, origin = http_probe
    transport = shared_transport(counter, policy, origin, timeout=0.2)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(TimeoutError):
            async with client.stream("GET", origin + "/stream") as response:
                iterator = response.aiter_bytes()
                assert await anext(iterator) == b"first\n"
                await anext(iterator)
        await asyncio.wait_for(probe.closed.wait(), 1)
    assert not transport._relays
    assert await redis.zcard(counter._bucket("stream", "leases")) == 1


async def test_policy_lookup_shares_request_deadline(meter, http_probe, monkeypatch):
    counter, policy, _ = meter
    probe, origin = http_probe
    original = counter.current_policy

    async def delayed_policy():
        await asyncio.sleep(0.3)
        return await original()

    monkeypatch.setattr(counter, "current_policy", delayed_policy)
    transport = shared_transport(counter, policy, origin, timeout=0.4)
    started = asyncio.get_running_loop().time()
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(TimeoutError):
            async with client.stream("GET", origin + "/stream") as response:
                async for _ in response.aiter_bytes():
                    pass
        await asyncio.wait_for(probe.closed.wait(), 1)
    assert asyncio.get_running_loop().time() - started < 0.65


async def test_response_byte_bound_is_explicit_and_closes_lease(meter, http_probe):
    counter, policy, redis = meter
    _, origin = http_probe
    module = importlib.import_module("muad_platform_sdk.shared_host_transport")
    async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin, max_bytes=3)) as client:
        with pytest.raises(module.HostResponseTooLarge):
            await client.get(origin + "/proxy")
    assert await redis.zcard(counter._bucket("stream", "leases")) == 1


async def test_https_verification_is_preserved(meter, tmp_path):
    from tests.sdk.test_requests_control_lifecycle import tls_context
    from tests.sdk.test_shared_http_transport import HTTPProbe

    counter, policy, _ = meter
    context, certificate = tls_context(tmp_path)
    verify = ssl.create_default_context(cafile=certificate)
    probe = HTTPProbe()
    server = await asyncio.start_server(probe.handle, "127.0.0.1", 0, ssl=context)
    origin = f"https://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        verified_transport = shared_transport(counter, policy, origin, verify=verify)
        async with httpx.AsyncClient(transport=verified_transport) as client:
            assert (await client.get(origin + "/proxy")).content == b"business"
        async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(origin + "/proxy")
        assert probe.calls == 1
    finally:
        server.close()
        await server.wait_closed()


async def test_distinct_clients_share_frozen_invocation_budget(meter, http_probe):
    counter, _, redis = meter
    probe, origin = http_probe
    policy = await policy_limit(counter, 8)
    await counter.register_execution("execution", max_requests=3)
    await counter.register_invocation("execution", "script", max_requests=2)
    invocation = TrustedInvocation("execution", "script", "segment")
    for index in range(3):
        transport = shared_transport(counter, policy, origin, invocation=invocation)
        async with httpx.AsyncClient(transport=transport) as client:
            if index < 2:
                assert (await client.get(origin + "/proxy")).status_code == 429
            else:
                with pytest.raises(SharedLimitExceeded) as failure:
                    await client.get(origin + "/proxy")
                assert failure.value.scope == "invocation"
    assert probe.calls == 2
    assert await redis.get(counter._bucket("stream", "total")) == "2"


async def test_controlled_request_with_missing_guard_never_sends(meter, http_probe):
    counter, policy, redis = meter
    probe, origin = http_probe
    await redis.delete(counter._key("guard"))
    async with httpx.AsyncClient(transport=shared_transport(counter, policy, origin)) as client:
        with pytest.raises(MeteringUnavailable):
            await client.get(origin + "/proxy")
    assert probe.calls == 0


async def test_cancel_before_headers_closes_physical_connection(meter):
    counter, policy, redis = meter
    started, closed = asyncio.Event(), asyncio.Event()

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            started.set()
            await reader.read()
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    transport = shared_transport(counter, policy, origin)
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            call = asyncio.create_task(client.get(origin + "/slow"))
            try:
                await asyncio.wait_for(started.wait(), 1)
            finally:
                call.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await call
            await asyncio.wait_for(closed.wait(), 1)
        assert not transport._relays
        assert await redis.zcard(counter._bucket("stream", "leases")) == 1
    finally:
        server.close()
        await server.wait_closed()


async def test_one_stream_deadline_allows_slow_socket_cleanup(meter, http_probe):
    counter, policy, _ = meter
    probe, origin = http_probe
    async with httpx.AsyncClient(trust_env=False) as pool:
        real_send = pool.send

        async def send(*args, **kwargs):
            response = await real_send(*args, **kwargs)
            real_close = response.aclose

            async def close():
                await asyncio.sleep(.04)
                await real_close()

            response.aclose = close
            return response

        pool.send = send
        transport = shared_transport(counter, policy, origin, timeout=.2, client=pool)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(TimeoutError):
                async with client.stream("GET", origin + "/stream") as response:
                    iterator = response.aiter_bytes()
                    assert await anext(iterator) == b"first\n"
                    await anext(iterator)
            await asyncio.wait_for(probe.closed.wait(), 1)
