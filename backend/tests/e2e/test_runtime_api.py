from __future__ import annotations
from tests.runtime_helpers import TEST_POSTGRES_DSN

import json

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.runtime import create_app
from fluxion.errors.console import RUNTIME_APPLICATION_ERROR
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RuntimeApplicationService,
)


@pytest.mark.asyncio
async def test_runtime_api_uses_unified_envelope_and_sse_stream() -> None:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store, cache_ttl_seconds=600)
    await service.initialize()
    try:
        await service.create_runtime_profile(
            CreateRuntimeProfileRequest(
                tenant_id="tenant-a",
                runtime_profile_id="assistant",
                version="1",
                request_timeout_ms=1_000,
                default=True,
            )
        )
        from tests.runtime_helpers import seed_agent_definition
        await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")

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
            health = await client.get("/healthz", headers={"X-Request-ID": "req_11111111111111111111111111111111"})
            response = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "hello",
                    "agent_definition_id": "assistant",
                },
                headers={"X-Request-ID": "req_22222222222222222222222222222222"},
            )
            stream = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs:stream",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "stream",
                    "agent_definition_id": "assistant",
                },
                headers={"X-Request-ID": "req_33333333333333333333333333333333"},
            )
            override = await client.post(
                "/internal/v1/runtime-profiles/assistant/runs",
                json={
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "input": "hello",
                    "agent_definition_id": "assistant",
                },
                headers={"X-Request-ID": "req_44444444444444444444444444444444", "X-Tenant-ID": "tenant-b"},
            )

        assert health.status_code == 200
        assert health.json()["request_id"] == "req_11111111111111111111111111111111"
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req_22222222222222222222222222222222"
        payload = response.json()
        assert payload["code"] == 0
        assert payload["message"] == "success"
        assert payload["request_id"] == "req_22222222222222222222222222222222"
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
        # TASK-A104：agent 路由后，tenant-b 无同名 agent → agent_not_found（非旧 profile 版本缺失）。
        assert "agent_not_found" in override.json()["message"]
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
