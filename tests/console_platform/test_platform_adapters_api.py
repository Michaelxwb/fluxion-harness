from httpx import AsyncClient
from muad_console_platform.main import app

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers


async def test_list_and_get_adapter_metadata(client: AsyncClient, tenant: TenantContext) -> None:
    listing = await client.get("/api/v1/platform-adapters", headers=_headers(tenant))
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert data["page"] == 1
    assert data["total"] == 1
    entry = data["items"][0]
    assert entry["key"] == "generic-http"
    assert entry["session_mode"] == "NONE"
    assert entry["credential_schema"]["properties"]["token"]["x-secret"] is True
    assert "auth_scheme" in entry["platform_config_schema"]["properties"]

    detail = await client.get("/api/v1/platform-adapters/generic-http", headers=_headers(tenant))
    assert detail.status_code == 200
    assert detail.json()["data"] == entry


async def test_e01_unknown_adapter_key_returns_platform_adapter_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.get("/api/v1/platform-adapters/not-registered", headers=_headers(tenant))
    assert response.status_code == 404
    assert response.json()["code"] == "PLATFORM_ADAPTER_NOT_FOUND"


async def test_page_size_out_of_range_is_rejected(client: AsyncClient, tenant: TenantContext) -> None:
    response = await client.get(
        "/api/v1/platform-adapters", params={"page_size": 101}, headers=_headers(tenant)
    )
    assert response.status_code == 422
    assert app.state.platform_adapters.get("generic-http").key == "generic-http"
