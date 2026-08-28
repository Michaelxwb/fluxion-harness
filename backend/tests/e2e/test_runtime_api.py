from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.runtime import create_app
from fluxion.errors.console import RUNTIME_APPLICATION_ERROR
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RuntimeApplicationService,
)


@pytest.mark.asyncio
async def test_runtime_api_uses_unified_envelope_and_sse_stream() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = RuntimeApplicationService.create_dev_bundle(store, cache_ttl_seconds=600)
    await service.initialize()
    try:
        await service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
                request_timeout_ms=1_000,
            )
        )
        from tests.runtime_helpers import seed_agent_definition
        await seed_agent_definition(store, provider_id="dev.echo")

        await service.publish_runtime_profile(
            PublishRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
            )
        )
        app = create_app(service)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/healthz", headers={"X-Request-ID": "req-health"})
            response = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "hello",
                },
                headers={"X-Request-ID": "req-run"},
            )
            stream = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs:stream",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "stream",
                },
                headers={"X-Request-ID": "req-stream"},
            )
            override = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "hello",
                },
                headers={"X-Request-ID": "req-override", "X-Tenant-ID": "tenant-b"},
            )

        assert health.status_code == 200
        assert health.json()["request_id"] == "req-health"
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req-run"
        payload = response.json()
        assert payload["code"] == 0
        assert payload["message"] == "success"
        assert payload["request_id"] == "req-run"
        # 模型名随 MODEL 资源链（TASK-004/008）；DevEcho 回显 provider 默认名。
        assert payload["data"]["output"] == "dev: hello"
        assert payload["data"]["runtime_profile_version"] == "1"
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(stream.text)
        assert [name for name, _ in events] == ["started", "completed"]
        completed = next(data for name, data in events if name == "completed")
        assert completed["output"] == "dev: stream"  # 模型名归 MODEL 链（TASK-004/008）
        assert override.status_code == 400
        assert override.json()["code"] == RUNTIME_APPLICATION_ERROR
        assert "resource_version_not_found" in override.json()["message"]
    finally:
        await service.close()


def _parse_sse(text: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        name = next((line.removeprefix("event: ") for line in lines if line.startswith("event: ")), None)
        data = next((line.removeprefix("data: ") for line in lines if line.startswith("data: ")), None)
        if name is None or data is None:
            continue
        events.append((name, json.loads(data)))
    return events
