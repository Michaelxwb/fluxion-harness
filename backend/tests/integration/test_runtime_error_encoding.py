"""Runtime HTTP 与 SSE 错误编码统一（TASK-020 / ADR-A015）验收测试。

覆盖 S-ERR-01 / E-ERR-01：
- 真实边界：真实 Runtime FastAPI 异常处理 → HTTP/SSE 编码器；
  真实异常映射/脱敏 → HTTP/SSE 响应。
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.runtime import create_app as create_runtime_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.runtime_app import RuntimeApplicationService
from tests.runtime_helpers import TEST_POSTGRES_DSN


async def _service() -> tuple[RuntimeApplicationService, object]:
    from fluxion.services.runtime_app import (
        CreateRuntimeProfileRequest,
        PublishRuntimeProfileRequest,
    )
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
            tenant_id="tenant-a", runtime_profile_id="assistant", version="1"
        )
    )
    return service, store


def _ids(char: str) -> tuple[str, str, str]:
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


@pytest.mark.asyncio
async def test_S_ERR_01_same_exception_same_encoding_http_and_sse() -> None:
    """S-ERR-01：相同异常在 HTTP/SSE 下产生相同 code/slug/安全文案与关联ID。"""
    service, store = await _service()
    try:
        app = create_runtime_app(service)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://runtime") as client:
            request_id, trace_id, execution_id = _ids("a")
            http_resp = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "s-err",
                    "input": "hello",
                    "agent_definition_id": "ghost-agent",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "execution_id": execution_id,
                },
                headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id},
            )
            sse_resp = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs:stream",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "s-err",
                    "input": "hello",
                    "agent_definition_id": "ghost-agent",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "execution_id": execution_id,
                },
                headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id},
            )
        assert http_resp.status_code == 404
        body = http_resp.json()
        assert body["code"] == 40_001
        assert body["error"] == "agent_not_found"
        assert body["request_id"] == request_id
        assert "traceback" not in body["message"].lower()

        assert sse_resp.status_code == 200
        frames = [
            block
            for block in sse_resp.text.split("\n\n")
            if block.startswith("event: error")
        ]
        assert len(frames) == 1
        error_data = json.loads(frames[0].split("data: ", 1)[1])
        assert error_data["code"] == 40_001
        assert error_data["error"] == "agent_not_found"
        assert error_data["request_id"] == request_id
    finally:
        await service.close()
        await store.close()


@pytest.mark.asyncio
async def test_E_ERR_01_unexpected_exception_safe_no_leak() -> None:
    """E-ERR-01：未知异常安全（固定文案、无敏感原文）；状态码正确。"""
    service, store = await _service()
    real_run = service.run

    async def exploding_run(request):  # type: ignore[no-untyped-def]
        raise ValueError("DSN postgres://mmuser:S3CR3T@localhost/db leaked")

    service.run = exploding_run  # type: ignore[method-assign]
    try:
        app = create_runtime_app(service)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://runtime") as client:
            request_id, trace_id, execution_id = _ids("b")
            resp = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "s-err",
                    "input": "hello",
                    "agent_definition_id": "assistant",
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "execution_id": execution_id,
                },
                headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == 39_001
        assert body["error"] == "internal_error"
        assert "S3CR3T" not in body["message"]
        assert "postgres" not in body["message"]
        assert body["request_id"] == request_id
    finally:
        service.run = real_run  # type: ignore[method-assign]
        await service.close()
        await store.close()
