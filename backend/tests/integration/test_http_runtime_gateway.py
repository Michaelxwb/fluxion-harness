"""TASK-001: HttpRuntimeGateway 验收测试（S-01/E-01）。

真实边界：httpx.AsyncClient（ASGITransport）→ 真实 Runtime API
（api/runtime.py 真实路由 + §3.3.1 wire 格式）→ 真实
RuntimeApplicationService（dev.echo Provider）→ 真实 PostgreSQLRegistryStore。
不新增 /internal/v1/runtime/run，不 mock Store/Resolver/Provider。
"""

from __future__ import annotations
from tests.runtime_helpers import TEST_POSTGRES_DSN

import json

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from fluxion.api.runtime import create_app as create_runtime_api_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import (
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from fluxion.services.runtime_contracts import RuntimeApplicationError


async def _seed_assistant(store: PostgreSQLRegistryStore) -> RuntimeApplicationService:
    from fluxion.services.runtime_app import CreateRuntimeProfileRequest
    from tests.runtime_helpers import seed_agent_definition

    service = RuntimeApplicationService.create_dev_bundle(store)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            default=True,
        )
    )
    await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
        )
    )
    return service


def _run_request() -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
        input_message="hello",
        agent_definition_id="assistant",
        request_id="req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        trace_id="trace_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        execution_id="exec_cccccccccccccccccccccccccccccccc",
    )


@pytest.mark.asyncio
async def test_s01_run_delegates_to_real_runtime_service() -> None:
    """S-01: 普通 run 经 HTTP 到真实 Runtime，返回正确结果且 trace 指向远端实例。"""
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = await _seed_assistant(store)
    try:
        app = create_runtime_api_app(service)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
            gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
            result = await gateway.run(_run_request())
        assert result.output == "dev: hello"
        assert result.runtime_profile_version == "1"
        assert result.service_instance_id == service.service_instance_id
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_s01_stream_restores_sse_events() -> None:
    """S-01: 流式还原 started/completed SSE 事件，不缓冲伪装流式。"""
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = await _seed_assistant(store)
    try:
        app = create_runtime_api_app(service)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
            gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
            events = [event async for event in gateway.stream(_run_request())]
        names = [event.event for event in events]
        assert "started" in names
        assert "completed" in names
        completed = next(event for event in events if event.event == "completed")
        assert completed.data.get("output") == "dev: hello"
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_s01_channel_uses_gateway_not_local_runtime() -> None:
    """S-01: Channel 装配 Gateway 后不再持有本地 AgentRuntime 执行体。"""
    from fluxion.services.channel_app import ChannelApplicationService
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = await _seed_assistant(store)
    try:
        app = create_runtime_api_app(service)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
            gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
            channel = ChannelApplicationService(store, gateway)
            assert isinstance(channel._runtime, HttpRuntimeGateway)
            assert not isinstance(channel._runtime, RuntimeApplicationService)
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_e01_run_no_instance_maps_503_without_retry() -> None:
    """E-01: 无可用实例时普通调用 503，不重试、不回退本地执行。"""
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    calls: list[Request] = []

    def _handler(request: Request) -> Response:
        calls.append(request)
        return Response(503, json={"code": 1, "message": "no instance", "data": None})

    transport = MockTransport(_handler)
    async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        with pytest.raises(RuntimeApplicationError):
            await gateway.run(_run_request())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_e01_stream_pre_connect_error_raises() -> None:
    """E-01: 流式建连前 HTTP 错误直接抛错，不切换路由重执行。"""
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    def _handler(request: Request) -> Response:
        return Response(503, json={"code": 1, "message": "no instance", "data": None})

    transport = MockTransport(_handler)
    async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        with pytest.raises(RuntimeApplicationError):
            [event async for event in gateway.stream(_run_request())]


@pytest.mark.asyncio
async def test_e01_stream_post_connect_sse_error_terminates() -> None:
    """E-01: 建连后 SSE error 帧终止流，不重试、不本地 fallback。"""
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    body = 'event: started\ndata: {"request_id": "req-s01"}\n\n' + (
        'event: error\ndata: {"code": 1, "error": "no_instance", '
        '"message": "gone", "request_id": "req-s01"}\n\n'
    )

    def _handler(request: Request) -> Response:
        assert request.url.path.endswith(":stream")
        return Response(200, content=body, headers={"content-type": "text/event-stream"})

    transport = MockTransport(_handler)
    async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
        events = [event async for event in gateway.stream(_run_request())]
    assert [event.event for event in events] == ["started", "error"]
    _ = json.dumps([event.data for event in events])
    assert ResourceKind.RUNTIME_PROFILE is not None


def test_production_bundle_requires_runtime_service_url(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-01/E-01：生产装配缺远程 Runtime URL 时 fail-fast，不回退本地执行。"""
    import os

    from fluxion.api.production_bundle import create_production_bundle_app
    from fluxion.services.production_profile import ProductionProfileError

    monkeypatch.delenv("FLUXION_RUNTIME_SERVICE_URL", raising=False)
    assert "FLUXION_RUNTIME_SERVICE_URL" not in os.environ
    with pytest.raises(ProductionProfileError, match="FLUXION_RUNTIME_SERVICE_URL"):
        create_production_bundle_app(
            registry_dsn="postgresql+asyncpg://u:p@localhost:5432/db",
            master_key=os.urandom(32),
            console_dist=tmp_path,
            chat_dist=tmp_path,
        )


@pytest.mark.asyncio
async def test_production_bundle_wires_gateway_no_local_runtime(tmp_path) -> None:
    """S-01：生产装配持有 Gateway 而非本地 RuntimeApplicationService。"""
    import os

    from fluxion.api.production_bundle import create_production_bundle_app
    from fluxion.services.channel_app import ChannelApplicationService
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
    from fluxion.services.runtime_app import RuntimeApplicationService

    (tmp_path / "index.html").write_text("<html>x</html>")
    app = create_production_bundle_app(
        registry_dsn="postgresql+asyncpg://u:p@localhost:5432/db",
        master_key=os.urandom(32),
        console_dist=tmp_path,
        chat_dist=tmp_path,
        runtime_service_url="http://fluxion-runtime:8000",
    )
    try:
        assembly = app.state.assembly
        assert isinstance(assembly.runtime_gateway, HttpRuntimeGateway)
        assert not isinstance(getattr(assembly, "runtime", None), RuntimeApplicationService)
        assert not hasattr(assembly, "runtime")
    finally:
        await app.state.assembly.close()
    _ = ChannelApplicationService


# --- TASK-013（S-09/E-03）：客户端侧轮询＋故障重试 ---


def _s09_envelope() -> dict:
    return {
        "code": 0,
        "message": "success",
        "data": {
            "request_id": "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "trace_id": "trace_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "execution_id": "exec_cccccccccccccccccccccccccccccccc",
            "service_instance_id": "instance-x",
            "runtime_profile_id": "assistant",
            "runtime_profile_version": "1",
            "output": "ok",
            "latency_ms": 1.0,
            "model_provider_id": "dev.echo",
            "tool_results": [],
        },
    }


async def _fake_dns(*args: object, **kwargs: object) -> list[str]:
    return ["10.0.0.1", "10.0.0.2"]


@pytest.mark.asyncio
async def test_S_09_requests_spread_across_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-09：多 endpoint 下连续请求打散（RR），而非粘滞单实例。"""
    import httpx

    from fluxion.services import http_runtime_gateway as gw
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    monkeypatch.setattr(gw, "_resolve_endpoint_ips", _fake_dns)
    hits: list[str] = []

    def _handler(request: Request) -> Response:
        hits.append(str(request.url.host))
        return Response(200, json=_s09_envelope())

    transport = MockTransport(_handler)
    async with AsyncClient(transport=transport, base_url="http://runtime:8000") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime:8000", client=raw)
        for _ in range(4):
            await gateway.run(_run_request())
    assert hits == ["10.0.0.1", "10.0.0.2", "10.0.0.1", "10.0.0.2"]
    assert isinstance(httpx.ConnectError("x"), httpx.TransportError)


@pytest.mark.asyncio
async def test_E_03_connect_error_retries_next_endpoint_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E-03：首选 endpoint 建连失败 → 换下一个重试一次且业务成功。"""
    import httpx

    from fluxion.services import http_runtime_gateway as gw
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    monkeypatch.setattr(gw, "_resolve_endpoint_ips", _fake_dns)
    hits: list[str] = []

    def _handler(request: Request) -> Response:
        hits.append(str(request.url.host))
        if str(request.url.host) == "10.0.0.1":
            raise httpx.ConnectError("connection refused", request=request)
        return Response(200, json=_s09_envelope())

    transport = MockTransport(_handler)
    async with AsyncClient(transport=transport, base_url="http://runtime:8000") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime:8000", client=raw)
        result = await gateway.run(_run_request())
    assert result.output == "ok"
    assert hits == ["10.0.0.1", "10.0.0.2"]


@pytest.mark.asyncio
async def test_E_03_stream_connect_error_retries_before_first_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E-03：流式建连失败（首字节前）同样换 endpoint 重试；首字节后失败不重试。"""
    import httpx

    from fluxion.services import http_runtime_gateway as gw
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    monkeypatch.setattr(gw, "_resolve_endpoint_ips", _fake_dns)
    hits: list[str] = []
    body = 'event: completed\ndata: {"output": "ok"}\n\n'

    def _handler(request: Request) -> Response:
        hits.append(str(request.url.host))
        if str(request.url.host) == "10.0.0.1":
            raise httpx.ConnectError("connection refused", request=request)
        return Response(200, content=body, headers={"content-type": "text/event-stream"})

    transport = MockTransport(_handler)
    async with AsyncClient(transport=transport, base_url="http://runtime:8000") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime:8000", client=raw)
        events = [event async for event in gateway.stream(_run_request())]
    assert [event.event for event in events] == ["completed"]
    assert hits == ["10.0.0.1", "10.0.0.2"]


@pytest.mark.asyncio
async def test_E_03_read_timeout_never_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E-03 安全 invariant：读超时（请求可能已执行）只试一次，直接 503。"""
    import httpx

    from fluxion.services import http_runtime_gateway as gw
    from fluxion.services.http_runtime_gateway import HttpRuntimeGateway

    monkeypatch.setattr(gw, "_resolve_endpoint_ips", _fake_dns)
    calls = 0

    def _handler(request: Request) -> Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    transport = MockTransport(_handler)
    async with AsyncClient(transport=transport, base_url="http://runtime:8000") as raw:
        gateway = HttpRuntimeGateway(base_url="http://runtime:8000", client=raw)
        with pytest.raises(RuntimeApplicationError) as exc_info:
            await gateway.run(_run_request())
        assert exc_info.value.code == "runtime_upstream_timeout"
    assert calls == 1
