"""Gateway 到 Runtime 的 ID 透传（TASK-005 / ADR-A012）验收测试。

覆盖 S-ID-01：
- 真实边界：真实 httpx Gateway → 真实 FastAPI 路由 → RunRuntimeRequest；
- 关键断言：request_id/trace_id/execution_id 逐一等于传入值；HTTP/SSE 一致；
- 旧请求（无 ID）兼容：缺省补齐；非法 ID fail-closed。

观测方式：_RecordingService 继承真实 RuntimeApplicationService，仅记录收到的
RunRuntimeRequest 后委托 super() 执行——不 mock 运行服务、不改行为。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.runtime import create_app as create_runtime_api_app
from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
from fluxion.services.runtime_app import (
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
    RuntimeStreamEvent,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN


def _valid(kind: str, char: str) -> str:
    return f"{kind}_{char * 32}"


async def _seed_service() -> RuntimeApplicationService:
    from fluxion.registry import PostgreSQLRegistryStore
    from fluxion.services.runtime_app import CreateRuntimeProfileRequest
    from tests.runtime_helpers import seed_agent_definition

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            request_timeout_ms=10_000,
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


class _RecordingService(RuntimeApplicationService):
    """真实服务 + 记录入口 RunRuntimeRequest（行为不变，仅观测）。"""

    def __init__(self, inner: RuntimeApplicationService) -> None:
        self._inner = inner
        self.seen: list[RunRuntimeRequest] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run(self, request: RunRuntimeRequest):  # type: ignore[no-untyped-def]
        self.seen.append(request)
        return await self._inner.run(request)

    async def stream(  # type: ignore[no-untyped-def]
        self, request: RunRuntimeRequest
    ) -> AsyncIterator[RuntimeStreamEvent]:
        self.seen.append(request)
        async for event in self._inner.stream(request):
            yield event


def _request() -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
        input_message="hello",
        agent_definition_id="assistant",
        request_id=_valid("req", "a"),
        trace_id=_valid("trace", "b"),
        execution_id=_valid("exec", "c"),
    )


@pytest.mark.asyncio
async def test_S_ID_01_run_identity_passthrough() -> None:
    """S-ID-01：run 路径三 ID 逐一等于传入值。"""
    service = await _seed_service()
    try:
        recording = _RecordingService(service)
        app = create_runtime_api_app(recording)  # type: ignore[arg-type]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
            gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
            sent = _request()
            await gateway.run(sent)
        assert len(recording.seen) == 1
        received = recording.seen[0]
        assert received.request_id == sent.request_id
        assert received.trace_id == sent.trace_id
        assert received.execution_id == sent.execution_id
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_ID_01_stream_identity_consistent_with_run() -> None:
    """S-ID-01：stream 路径 started 事件 ID 与 run 一致（HTTP/SSE 一致）。"""
    service = await _seed_service()
    try:
        recording = _RecordingService(service)
        app = create_runtime_api_app(recording)  # type: ignore[arg-type]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
            gateway = HttpRuntimeGateway(base_url="http://runtime", client=raw)
            sent = _request()
            events = [event async for event in gateway.stream(sent)]
        assert len(recording.seen) == 1
        received = recording.seen[0]
        assert received.request_id == sent.request_id
        assert received.trace_id == sent.trace_id
        assert received.execution_id == sent.execution_id
        started = next(e for e in events if e.event == "started")
        assert started.data["request_id"] == sent.request_id
        assert started.data["execution_id"] == sent.execution_id
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_ID_01_legacy_request_without_ids_compatible() -> None:
    """S-ID-01：旧请求（无 ID 字段）兼容——缺省补齐且带正确前缀。"""
    service = await _seed_service()
    try:
        app = create_runtime_api_app(service)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
            response = await raw.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "hello",
                    "agent_definition_id": "assistant",
                },
            )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["request_id"].startswith("req_")
        assert data["trace_id"].startswith("trace_")
        assert data["execution_id"].startswith("exec_")
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_ID_01_invalid_identity_fails_closed() -> None:
    """S-ID-01：非法 ID fail-closed（400 + slug，不静默替换）。"""
    service = await _seed_service()
    try:
        app = create_runtime_api_app(service)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://runtime") as raw:
            response = await raw.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "hello",
                    "agent_definition_id": "assistant",
                    "trace_id": "bogus",
                },
                headers={"X-Request-ID": _valid("req", "a")},
            )
        assert response.status_code == 400
        assert response.json()["error"] == "request_identity_invalid"
    finally:
        await service.close()
