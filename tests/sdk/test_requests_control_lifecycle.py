"""Real socket interruption and host-authorized unmanaged requests."""
import asyncio
import importlib
import ssl
import subprocess

import pytest
import requests
from muad_contracts.shared_egress import TrustedInvocation
from muad_platform_sdk.egress_ipc import EgressTarget, InvocationEgressBroker, IPCAdmissionClient
from muad_platform_sdk.egress_ipc_client import IPCAdmissionError

from tests.sdk.test_egress_ipc import ipc_meter as ipc_meter
from tests.sdk.test_requests_egress_hook import hook

pytestmark = pytest.mark.integration


def tls_context(tmp_path):
    certificate, key = tmp_path / "certificate.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    return context, str(certificate)


@pytest.fixture
async def network_probe(request, tmp_path):
    seen, writers = [], []
    closed, started = asyncio.Event(), asyncio.Event()

    async def handle(reader, writer):
        writers.append(writer)
        path = ""
        try:
            data = await reader.readuntil(b"\r\n\r\n")
            path = data.split(b" ")[1].decode()
            seen.append(path)
            started.set()
            if path in ("/slow_headers", "/slow_body"):
                if path == "/slow_body":
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\na")
                    await writer.drain()
                await reader.read()
                closed.set()
            else:
                locations = {"/redirect": "/free", "/free_redirect": "/business_redirect",
                             "/business_redirect": "/free"}
                if path in locations:
                    writer.write(("HTTP/1.1 302 Found\r\nLocation: " + locations[path] +
                                  "\r\nContent-Length: 0\r\n\r\n").encode())
                else:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
        finally:
            if path in ("/slow_headers", "/slow_body"):
                closed.set()
            writer.close()
            await writer.wait_closed()

    context, verify = tls_context(tmp_path) if getattr(request, "param", False) else (None, True)
    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context,
                                         ssl_shutdown_timeout=0.3 if context else None)
    scheme = "https" if context else "http"
    try:
        yield f"{scheme}://127.0.0.1:{server.sockets[0].getsockname()[1]}", seen, started, closed, verify
    finally:
        server.close()
        for writer in writers:
            writer.close()
        await server.wait_closed()


def make_host(meter, policy, origin, *, deadline=3, allow_free=False):
    def resolve(attempt):
        if attempt.origin != origin:
            return None
        if allow_free and attempt.path.startswith("/free"):
            api = importlib.import_module("muad_platform_sdk.egress_ipc")
            return api.UncontrolledTarget()
        return EgressTarget("platform", "GET /assets")

    return InvocationEgressBroker(meter=meter, policy=policy,
                                   invocation=TrustedInvocation("execution", "script", "segment"),
                                   resolve=resolve, deadline_seconds=deadline, cleanup_seconds=0.5,
                                   renewal_interval_seconds=0.05)


async def test_host_authorized_free_request_does_not_access_limiter(ipc_meter, network_probe):
    redis, meter, policy = ipc_meter
    origin, seen, _, _, verify = network_probe
    async with make_host(meter, policy, origin, allow_free=True) as host:
        await redis.delete(meter._key("guard"))
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                session.verify = verify
                assert session.get(origin + "/free").content == b"ok"

        await asyncio.to_thread(call)
    assert seen == ["/free"]
    assert await redis.get(meter._bucket("assets", "total")) == "0"


@pytest.mark.parametrize("path,expected,count", [
    ("/redirect", ["/redirect"], "1"),
    ("/free_redirect", ["/free_redirect", "/business_redirect"], "1"),
])
async def test_controlled_redirect_cannot_downgrade_to_free(ipc_meter, network_probe, path, expected, count):
    redis, meter, policy = ipc_meter
    origin, seen, _, _, verify = network_probe
    async with make_host(meter, policy, origin, allow_free=True) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                session.verify = verify
                with pytest.raises(IPCAdmissionError, match="IPC_TARGET_DENIED"):
                    session.get(origin + path)

        await asyncio.to_thread(call)
    assert seen == expected
    assert await redis.get(meter._bucket("assets", "total")) == count


@pytest.mark.parametrize("network_probe", [False, True], indirect=True)
@pytest.mark.parametrize("path", ["/slow_headers", "/slow_body"])
async def test_execution_deadline_interrupts_real_requests_io(ipc_meter, network_probe, path):
    _, meter, policy = ipc_meter
    origin, _, _, closed, verify = network_probe
    async with make_host(meter, policy, origin, deadline=0.25) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env = False
                session.verify = verify
                with pytest.raises(IPCAdmissionError, match="IPC_EXECUTION_EXPIRED"):
                    assert session.get(origin + path, timeout=0.8).content == b"ok"

        await asyncio.wait_for(asyncio.to_thread(call), 3)
        await asyncio.wait_for(closed.wait(), 1)


async def test_shorter_caller_timeout_stays_a_native_requests_error(ipc_meter, network_probe):
    _, meter, policy = ipc_meter
    origin, _, _, closed, verify = network_probe
    async with make_host(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env, session.verify = False, verify
                with pytest.raises(requests.exceptions.ReadTimeout):
                    session.get(origin + "/slow_headers", timeout=0.1)

        await asyncio.wait_for(asyncio.to_thread(call), 2)
        await asyncio.wait_for(closed.wait(), 1)


@pytest.mark.parametrize("path", ["/slow_headers", "/slow_body"])
async def test_ipc_disconnect_interrupts_unfinished_transport(ipc_meter, network_probe, path):
    _, meter, policy = ipc_meter
    origin, _, started, closed, verify = network_probe
    host = make_host(meter, policy, origin)
    await host.__aenter__()
    client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

    def call():
        with hook(client), requests.Session() as session:
            session.trust_env, session.verify = False, verify
            with pytest.raises(IPCAdmissionError, match="IPC_UNAVAILABLE"):
                assert session.get(origin + path, timeout=0.8).content == b"ok"

    task = asyncio.create_task(asyncio.to_thread(call))
    stopped = False
    try:
        await asyncio.wait_for(started.wait(), 2)
        await host.__aexit__(None, None, None)
        stopped = True
        await asyncio.wait_for(task, 2)
        await asyncio.wait_for(closed.wait(), 1)
    finally:
        if not stopped:
            await host.__aexit__(None, None, None)
        await asyncio.wait_for(task, 2)


async def test_response_hook_can_make_independent_free_request(ipc_meter, network_probe):
    redis, meter, policy = ipc_meter
    origin, seen, _, _, verify = network_probe
    async with make_host(meter, policy, origin, allow_free=True) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env, session.verify = False, verify

                def report(response, *args, **kwargs):
                    assert session.get(origin + "/free").content == b"ok"
                    return response

                assert session.get(origin + "/final", hooks={"response": report}).content == b"ok"

        await asyncio.to_thread(call)
    assert seen == ["/final", "/free"]
    assert await redis.get(meter._bucket("assets", "total")) == "1"


async def test_redis_epoch_change_interrupts_unfinished_body(ipc_meter, network_probe):
    redis, meter, policy = ipc_meter
    origin, _, started, closed, verify = network_probe
    async with make_host(meter, policy, origin) as host:
        client = IPCAdmissionClient(host.socket_path, timeout_seconds=1)

        def call():
            with hook(client), requests.Session() as session:
                session.trust_env, session.verify = False, verify
                with pytest.raises(IPCAdmissionError, match="IPC_DEPENDENCY_UNAVAILABLE"):
                    assert session.get(origin + "/slow_body", timeout=0.8).content == b"ok"

        task = asyncio.create_task(asyncio.to_thread(call))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await redis.hset(meter._key("guard"), "epoch", "changed-generation")
            await asyncio.wait_for(task, 2)
            await asyncio.wait_for(closed.wait(), 1)
        finally:
            await asyncio.wait_for(task, 2)
