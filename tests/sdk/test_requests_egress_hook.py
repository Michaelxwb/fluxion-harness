"""Explicit hook installation with real requests/HTTP/Unix IPC/Redis."""
import asyncio
import importlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from muad_contracts.shared_egress import TrustedInvocation
from muad_platform_sdk.egress_ipc import EgressTarget, InvocationEgressBroker, IPCAdmissionClient

from tests.sdk.test_egress_ipc import ipc_meter as ipc_meter

pytestmark = pytest.mark.integration


@pytest.fixture
async def http_probe():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, dict(self.headers)))
            path = self.path.split("?")[0]
            status = {"/redirect": 302, "/foreign": 302, "/fail": 503, "/proxy": 429}.get(path, 200)
            self.send_response(status)
            if path == "/redirect":
                self.send_header("Location", "/final")
            if path == "/foreign":
                self.send_header("Location", "http://unauthorized.test/final")
            if path == "/proxy":
                self.send_header("Retry-After", "7")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)


def broker(meter, policy, origin, *, invocation_id="script"):
    def resolve(attempt):
        if attempt.origin != origin or attempt.method != "GET":
            return None
        if attempt.authority not in (None, "virtual.test"):
            return None
        return EgressTarget("platform", "GET /assets")

    return InvocationEgressBroker(meter=meter,
                                   invocation=TrustedInvocation("execution", invocation_id, "segment"),
                                   resolve=resolve, deadline_seconds=5, cleanup_seconds=0.5)


def hook(client):
    module = importlib.import_module("muad_platform_sdk.requests_egress_hook")
    return module.RequestsEgressHook(client)


async def test_redirects_are_counted_individually_and_auth_headers_preserved(ipc_meter, http_probe):
    redis, meter, policy = ipc_meter
    origin, seen = http_probe
    async with broker(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                session.cookies.set("test-session", "value")
                response = session.get(origin + "/redirect?search=kept", headers={
                    "X-CSRFToken": "fixed-test-csrf", "Cookie": "test-session=value", "Host": "virtual.test",
                })
                assert response.status_code == 200 and response.content == b"ok"

        await asyncio.to_thread(call)
    assert [path for path, _ in seen] == ["/redirect?search=kept", "/final"]
    for _, headers in seen:
        assert headers["X-CSRFToken"] == "fixed-test-csrf"
        assert headers["Cookie"] == "test-session=value"
        assert headers["Host"] == "virtual.test"
    assert await redis.get(meter._bucket("assets", "total")) == "2"


async def test_unauthorized_redirect_is_rejected_before_second_send(ipc_meter, http_probe):
    redis, meter, policy = ipc_meter
    origin, seen = http_probe
    async with broker(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                with pytest.raises(RuntimeError, match="IPC_TARGET_DENIED"):
                    session.get(origin + "/foreign")

        await asyncio.to_thread(call)
    assert len(seen) == 1
    assert await redis.get(meter._bucket("assets", "total")) == "1"


async def test_implicit_retries_are_explicitly_rejected_without_sending(ipc_meter, http_probe):
    redis, meter, policy = ipc_meter
    origin, seen = http_probe
    async with broker(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                session.mount("http://", requests.adapters.HTTPAdapter(max_retries=2))
                with pytest.raises(RuntimeError, match="UNSUPPORTED_REQUESTS_RETRY"):
                    session.get(origin + "/fail")

        await asyncio.to_thread(call)
    assert seen == []
    assert await redis.get(meter._bucket("assets", "total")) == "0"


async def test_explicit_retries_each_acquire_a_new_permit(ipc_meter, http_probe):
    redis, meter, policy = ipc_meter
    origin, seen = http_probe
    await meter.register_invocation("execution", "retry-script", max_requests=5)
    async with broker(meter, policy, origin, invocation_id="retry-script") as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                for _ in range(3):
                    response = session.get(origin + "/fail")
                    assert response.status_code == 503 and response.content == b"ok"

        await asyncio.to_thread(call)
    assert len(seen) == 3
    assert await redis.get(meter._bucket("assets", "total")) == "3"


async def test_stream_holds_permit_until_close_and_prepared_send_is_metered_once(ipc_meter, http_probe):
    redis, meter, policy = ipc_meter
    origin, seen = http_probe
    async with broker(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                prepared = session.prepare_request(requests.Request("GET", origin + "/final"))
                response = session.send(prepared, stream=True)
                assert response.raw.read(0) == b""
                with pytest.raises(RuntimeError, match="INFLIGHT"):
                    session.get(origin + "/final")
                response.close()
                next_response = session.get(origin + "/final")
                assert next_response.content == b"ok"

        await asyncio.to_thread(call)
    assert len(seen) == 2
    assert await redis.get(meter._bucket("assets", "total")) == "2"


async def test_proxy_result_is_unchanged_and_host_override_is_authorized(ipc_meter, http_probe):
    redis, meter, policy = ipc_meter
    origin, seen = http_probe
    async with broker(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                with pytest.raises(RuntimeError, match="IPC_TARGET_DENIED"):
                    session.get(origin + "/proxy", headers={"Host": "foreign.test"})
                response = session.get(origin + "/proxy")
                assert response.status_code == 429
                assert response.headers["Retry-After"] == "7"
                assert response.content == b"ok"

        await asyncio.to_thread(call)
    assert len(seen) == 1
    assert await redis.get(meter._bucket("assets", "total")) == "1"


async def test_custom_adapter_is_rejected_before_its_send(ipc_meter, http_probe):
    redis, meter, policy = ipc_meter
    origin, seen = http_probe

    class CustomAdapter(requests.adapters.HTTPAdapter):
        def send(self, *args, **kwargs):
            pytest.fail("custom adapter send must not run")

    async with broker(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.mount("http://", CustomAdapter())
                with pytest.raises(RuntimeError, match="UNSUPPORTED_REQUESTS_ADAPTER"):
                    session.get(origin + "/final")

        await asyncio.to_thread(call)
    assert seen == []
    assert await redis.get(meter._bucket("assets", "total")) == "0"


async def test_completion_ipc_failure_does_not_rewrite_received_proxy_result(ipc_meter, http_probe, caplog):
    _, meter, policy = ipc_meter
    origin, _ = http_probe
    async with broker(meter, policy, origin) as host:
        original_reply = host._reply

        async def drop_completion_ack(writer, value):
            if value == {"ok": True}:
                writer.close()
                await writer.wait_closed()
                return
            await original_reply(writer, value)

        host._reply = drop_completion_ack
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                response = session.get(origin + "/proxy")
                assert response.content == b"ok"
                assert response.status_code == 429
                assert response.headers["Retry-After"] == "7"

        await asyncio.to_thread(call)
    assert "requests_egress_completion_unconfirmed" in caplog.text
