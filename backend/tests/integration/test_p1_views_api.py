from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from tests.console_helpers import (
    console_stack,
    create_resource,
    publish_resource,
    tenant_headers,
)

from fluxion.api.console import create_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.runtime_contracts import PluginSummary


@pytest.mark.asyncio
async def test_S_C118_plugin_policy_view_lists_published_policies() -> None:
    # S-C118 场景：Plugin/Hook Policy P1 视图经真实 HTTP 读取 tenant Policy 资源。
    async with console_stack() as stack:
        created = await create_resource(
            stack.client,
            kind=ResourceKind.POLICY,
            resource_id="main-policy",
            spec={"name": "main-policy"},
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.POLICY,
            resource_id="main-policy",
        )
        response = await stack.client.get(
            "/api/v1/policies?page=1&page_size=20",
            headers=tenant_headers(),
        )

    assert created.status_code == 200
    assert published.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    items = payload["data"]["items"]
    assert len(items) == 1
    assert items[0]["policy_id"] == "main-policy"
    assert items[0]["name"] == "main-policy"
    assert items[0]["version"] == "1"
    assert items[0]["status"] == "published"
    assert items[0]["allowed_tools"] == []


@pytest.mark.asyncio
async def test_S_C118_plugin_policy_view_is_tenant_scoped() -> None:
    async with console_stack() as stack:
        for tenant_id in ("tenant-a", "tenant-b"):
            resource_id = f"{tenant_id}-policy"
            await create_resource(
                stack.client,
                kind=ResourceKind.POLICY,
                resource_id=resource_id,
                tenant_id=tenant_id,
                spec={"name": resource_id},
            )
            await publish_resource(
                stack.client,
                kind=ResourceKind.POLICY,
                resource_id=resource_id,
                tenant_id=tenant_id,
            )
        response = await stack.client.get(
            "/api/v1/policies?page=1&page_size=20",
            headers=tenant_headers(tenant_id="tenant-a"),
        )

    payload = response.json()
    assert payload["code"] == 0
    ids = [item["policy_id"] for item in payload["data"]["items"]]
    assert "tenant-a-policy" in ids
    assert "tenant-b-policy" not in ids


@pytest.mark.asyncio
async def test_S_C118_capability_view_enumerates_loaded_plugin_capabilities() -> None:
    async with _p1_stack(
        plugin_summaries=(
            PluginSummary("dev.echo", "model_provider", "trusted", "in_process"),
            PluginSummary("browser-model", "model_provider", "trusted", "in_process"),
        ),
        service_instance_id="instance-p1-test",
    ) as stack:
        response = await stack.client.get(
            "/api/v1/capabilities", headers=tenant_headers()
        )

    payload = response.json()
    assert payload["code"] == 0
    items = payload["data"]["items"]
    assert payload["data"]["total"] == 2
    ids = [item["capability_id"] for item in items]
    assert ids == ["model.dev.echo", "model.browser-model"]
    assert items[0]["kind"] == "model_provider"
    assert items[0]["status"] == "loaded"
    assert items[0]["provider_id"] == "dev.echo"


@pytest.mark.asyncio
async def test_S_C118_runtime_status_view_is_readonly_and_healthy() -> None:
    async with _p1_stack(
        plugin_summaries=(PluginSummary("dev.echo", "model_provider", "trusted", "in_process"),),
        service_instance_id="instance-p1-test",
    ) as stack:
        response = await stack.client.get(
            "/api/v1/runtime-status", headers=tenant_headers()
        )

    payload = response.json()
    assert payload["code"] == 0
    data = payload["data"]
    assert data["service_instance_id"] == "instance-p1-test"
    assert data["status"] == "healthy"
    assert data["provider_count"] == 1
    assert data["plugin_count"] == 1
    # Runtime Status 只观测不管理：无 Pod 生命周期字段
    assert "pod" not in data


@pytest.mark.asyncio
async def test_S_C118_users_channels_view_reuses_platform_users() -> None:
    async with console_stack() as stack:
        created = await stack.client.post(
            "/api/v1/platform-users",
            json={"platform_user_id": "alice", "display_name": "Alice"},
            headers=tenant_headers(),
        )
        response = await stack.client.get(
            "/api/v1/platform-users?page=1&page_size=20",
            headers=tenant_headers(),
        )

    assert created.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    users = payload["data"]["items"]
    assert len(users) == 1
    assert users[0]["platform_user_id"] == "alice"
    assert users[0]["display_name"] == "Alice"


@dataclass(slots=True)
class _P1Stack:
    client: AsyncClient
    service: ConsoleApplicationService
    store: SQLiteRegistryStore


@asynccontextmanager
async def _p1_stack(
    *,
    plugin_summaries: tuple[PluginSummary, ...] = (),
    service_instance_id: str | None = None,
) -> AsyncIterator[_P1Stack]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    service = ConsoleApplicationService(
        store,
        plugin_summaries=plugin_summaries,
        service_instance_id=service_instance_id,
    )
    await service.initialize()
    client = AsyncClient(
        transport=ASGITransport(app=create_app(service)),
        base_url="http://testserver",
    )
    try:
        yield _P1Stack(client=client, service=service, store=store)
    finally:
        await client.aclose()
        await service.close()
