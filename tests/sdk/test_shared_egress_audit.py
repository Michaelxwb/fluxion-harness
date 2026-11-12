"""Physical HTTP and Redis decisions must reach the shared audit observer."""

import asyncio

import httpx
import pytest
import requests
from muad_contracts.shared_egress import SharedEgressPolicy, TrustedInvocation
from muad_platform_sdk.egress_ipc import EgressTarget, InvocationEgressBroker
from muad_platform_sdk.egress_ipc_client import IPCAdmissionClient
from muad_platform_sdk.requests_egress_hook import RequestsEgressHook
from muad_platform_sdk.shared_host_transport import HostRoute, SharedHostTransport
from muad_platform_sdk.shared_http_transport import RequestLifetime
from muad_platform_sdk.shared_redis import SharedLimitExceeded

from tests.sdk.test_egress_ipc import ipc_meter as ipc_meter_fixture
from tests.sdk.test_requests_egress_hook import http_probe as requests_probe_fixture
from tests.sdk.test_shared_http_transport import http_probe as host_probe_fixture

ipc_meter = ipc_meter_fixture
requests_probe = requests_probe_fixture
host_probe = host_probe_fixture
pytestmark = pytest.mark.integration


async def limited_policy(meter):
    policy = SharedEgressPolicy.model_validate({"revision": 2, "rules": [{
        "id": "audit", "target_instance_id": "platform", "route_id": "GET /assets",
        "max_requests": 1,
    }]})
    await meter.publish(policy)
    return policy


async def test_httpx_reports_upstream_429_and_local_denial_once(ipc_meter, host_probe):
    _, meter, _ = ipc_meter
    policy = await limited_policy(meter)
    probe, origin = host_probe
    events = []

    async def observe(event):
        events.append(event)

    transport = SharedHostTransport(
        meter, public_rules_enabled=True, resolve=lambda _: HostRoute("platform", "GET /assets", policy),
        lifetime=RequestLifetime(2, .5, .05), observe=observe,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get(origin + "/proxy?secret=canary")).status_code == 429
        with pytest.raises(SharedLimitExceeded):
            await client.get(origin + "/proxy")
    assert probe.calls == 1
    assert [event.route_id for event in events] == ["GET /proxy", "GET /proxy"]
    assert len(events) == 2
    assert events[0].sent and events[0].status_code == 429 and events[0].result_status == "FAILED"
    assert not events[1].sent and events[1].result_status == "BLOCKED"
    assert "canary" not in repr(events)
    assert events[0].attempt_id != events[1].attempt_id


async def test_requests_reports_completion_and_denial_once(ipc_meter, requests_probe):
    _, meter, _ = ipc_meter
    policy = await limited_policy(meter)
    origin, seen = requests_probe
    events = []

    async def observe(event):
        events.append(event)

    async with InvocationEgressBroker(
        meter=meter, invocation=TrustedInvocation("execution", "script", "segment"),
        resolve=lambda _: EgressTarget("platform", "GET /assets", policy),
        deadline_seconds=5, cleanup_seconds=.5, observe=observe,
    ) as broker:
        client = IPCAdmissionClient(broker.socket_path, timeout_seconds=2)

        def call():
            with RequestsEgressHook(client), requests.Session() as session:
                session.trust_env = False
                assert session.get(origin + "/assets?token=canary").status_code == 200
                with pytest.raises(RuntimeError, match="IPC_LIMIT_TOTAL"):
                    session.get(origin + "/assets")

        await asyncio.to_thread(call)
    assert len(seen) == 1 and len(events) == 2
    assert events[0].sent and events[0].status_code == 200 and events[0].result_status == "COMPLETED"
    assert not events[1].sent and events[1].result_status == "BLOCKED"
    assert "canary" not in repr(events)


@pytest.mark.parametrize("upstream_error", [False, True], ids=["http-503", "connection-refused"])
async def test_requests_reports_actual_failures_without_exception_text(
    ipc_meter, requests_probe, upstream_error,
):
    _, meter, policy = ipc_meter
    origin, _ = requests_probe
    events = []

    async def observe(event):
        events.append(event)

    async with InvocationEgressBroker(
        meter=meter, invocation=TrustedInvocation("execution", "script", "segment"),
        resolve=lambda _: EgressTarget("platform", "GET /assets", policy),
        deadline_seconds=5, cleanup_seconds=.5, observe=observe,
    ) as broker:
        client = IPCAdmissionClient(broker.socket_path, timeout_seconds=2)

        def call():
            with RequestsEgressHook(client), requests.Session() as session:
                session.trust_env = False
                if upstream_error:
                    with pytest.raises(requests.ConnectionError):
                        session.get("http://127.0.0.1:9/assets?token=canary", timeout=1)
                else:
                    assert session.get(origin + "/fail?token=canary").status_code == 503

        await asyncio.to_thread(call)
    assert len(events) == 1 and events[0].sent and events[0].result_status == "FAILED"
    assert events[0].status_code == (None if upstream_error else 503)
    assert events[0].error_code == ("TRANSPORT_ERROR" if upstream_error else "HTTP_ERROR")
    assert "canary" not in repr(events)


@pytest.mark.parametrize("extra", [{"status_code": True}, {"headers": {"Cookie": "canary"}},
                                  {"error_code": "private exception canary"}])
def test_ipc_completion_rejects_unbounded_or_sensitive_metadata(extra):
    from muad_platform_sdk.egress_ipc_protocol import CompletionRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompletionRequest.model_validate({"op": "done", **extra})


@pytest.mark.parametrize("configured_route", ["__unmatched__", "GET /items/{id}"])
async def test_skill_requests_keep_each_actual_path_independent_of_rules(
    ipc_meter, requests_probe, configured_route,
):
    _, meter, policy = ipc_meter
    origin, seen = requests_probe
    events = []
    await meter.register_invocation("execution", "path-observer", max_requests=5)

    async def observe(event):
        events.append(event)

    async with InvocationEgressBroker(
        meter=meter, invocation=TrustedInvocation("execution", "path-observer", "segment"),
        resolve=lambda _: EgressTarget("__unmatched__", configured_route, policy),
        deadline_seconds=5, cleanup_seconds=.5, observe=observe,
    ) as broker:
        client = IPCAdmissionClient(broker.socket_path, timeout_seconds=2)

        def call():
            with RequestsEgressHook(client), requests.Session() as session:
                session.trust_env = False
                for path in ("/v1/rtt", "/api/v1/targets", "/items/123"):
                    assert session.get(origin + path + "?token=canary").status_code == 200

        await asyncio.to_thread(call)
    assert len(seen) == 3 and len(events) == 3
    assert [event.route_id for event in events] == [
        "GET /v1/rtt", "GET /api/v1/targets", "GET /items/123",
    ]
    assert all(event.result_status == "COMPLETED" for event in events)
    assert "canary" not in repr(events)
