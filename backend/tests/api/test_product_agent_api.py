"""TASK-003（phase1-closure）Product Agent API 验收测试。

S-04（E2E，fluxion-runtime-core / fluxion-console-api-contract / RULE-C-03）：
- `POST /api/v1/agents/{agent_id}/runs`（agent_id 主坐标）执行成功；
- `GET /api/v1/agents/{agent_id}` 产品面（displayName/description/能力）；
- 产品面响应零 `runtime_profile_id`（mechanics 内聚 internal）；
- `/internal/v1/runtime-profiles/{id}/runs` 仍可用于 internal/testing。

真实边界：真实 RuntimeApplicationService + AgentDefinitionRepository + SQLite
Registry；不 mock。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from tests.runtime_helpers import publish_resource, seed_agent_definition

from fluxion.registry import SQLiteRegistryStore
from fluxion.services.agents_app import ProductAgentApplicationService
from fluxion.services.runtime_app import RuntimeApplicationService


@pytest.fixture
async def stack() -> AsyncGenerator[tuple[AsyncClient, AsyncClient, SQLiteRegistryStore], None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=__import__("fluxion.resources", fromlist=["ResourceKind"]).ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。", provider_id="dev.echo")
    runtime = RuntimeApplicationService.create_dev_bundle(store)
    product = ProductAgentApplicationService(store=store, runtime=runtime)

    from fluxion.api.agents import create_app as create_agents_app
    from fluxion.api.runtime import create_app as create_runtime_app

    product_client = AsyncClient(
        transport=ASGITransport(create_agents_app(product)), base_url="http://product"
    )
    internal_client = AsyncClient(
        transport=ASGITransport(create_runtime_app(runtime)), base_url="http://internal"
    )
    try:
        yield product_client, internal_client, store
    finally:
        await product_client.aclose()
        await internal_client.aclose()
        await runtime.close()
        await store.close()


@pytest.mark.asyncio
async def test_s04_product_run_via_agent_coordinate(stack) -> None:
    product_client, _, _ = stack
    response = await product_client.post(
        "/api/v1/agents/assistant/runs",
        json={
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "session_id": "session-1",
            "input": "hello",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["output"]
    # 产品面零 runtime_profile_id 泄漏
    assert "runtime_profile_id" not in body


@pytest.mark.asyncio
async def test_s04_product_agent_face_has_no_mechanics(stack) -> None:
    product_client, _, _ = stack
    response = await product_client.get(
        "/api/v1/agents/assistant", headers={"X-Tenant-ID": "tenant-a"}
    )
    assert response.status_code == 200, response.text
    face = response.json()["data"]
    assert face["name"]
    assert face["available"] is True
    assert "runtime_profile_id" not in face
    assert "runtime_profile_ref" not in face


@pytest.mark.asyncio
async def test_s04_internal_route_still_available(stack) -> None:
    _, internal_client, _ = stack
    response = await internal_client.post(
        "/internal/v1/runtime-profiles/assistant/runs",
        json={
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "session_id": "session-i",
            "input": "internal ping",
            "agent_definition_id": "assistant",
        },
    )
    assert response.status_code == 200, response.text
    # 旧公开路径已关闭（防边界回退）
    legacy = await internal_client.post(
        "/api/v1/runtime-profiles/assistant/runs",
        json={
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "session_id": "session-i",
            "input": "x",
        },
    )
    assert legacy.status_code == 404
