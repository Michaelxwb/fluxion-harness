"""TASK-007 User Domain `/admin/users/*` 验收测试。

- BE-S-08（E2E）：创建 PlatformUser → Profile → Preferences → CapabilityGrant
  → User 360 五区聚合（真实链 API → UserDomainService → Store）。
- BE-S-10（E2E）：五区结构完整可见（Identity/Profile/Preferences/Capabilities/Policy）。
- BE-E-06（integration）：渠道身份未绑定 platform_user → 404 `user_not_bound`。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from fluxion.api.console import create_app as create_console_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.users import UserDomainService
from tests.console_helpers import tenant_headers


async def _admin_client(store: SQLiteRegistryStore) -> AsyncClient:
    console = ConsoleApplicationService(store)
    users = UserDomainService(store)
    app = create_console_app(console, user_service=users)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://console")


@pytest.mark.asyncio
async def test_be_s_08_create_profile_grant_then_360() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    async with await _admin_client(store) as client:
        created = await client.post(
            "/admin/users",
            json={"platform_user_id": "u-1", "display_name": "用户一"},
            headers=tenant_headers(request_id="req-s08-create"),
        )
        assert created.status_code == 200, created.text

        profile = await client.put(
            "/admin/users/u-1/profile",
            json={"display_name": "昵称A", "bio": "builder"},
            headers=tenant_headers(request_id="req-s08-profile"),
        )
        assert profile.status_code == 200
        assert profile.json()["data"]["version"] == 1

        prefs = await client.put(
            "/admin/users/u-1/preferences",
            json={"theme": "dark"},
            headers=tenant_headers(request_id="req-s08-pref"),
        )
        assert prefs.status_code == 200
        assert prefs.json()["data"]["preference_json"]["theme"] == "dark"

        grant = await client.post(
            "/admin/users/u-1/grants",
            json={
                "type": "mcp",
                "capability_ref": "weather",
                "version_pin": "1",
                "granted_scope": "invoke",
            },
            headers=tenant_headers(request_id="req-s08-grant"),
        )
        assert grant.status_code == 200, grant.text

        view = await client.get(
            "/admin/users/u-1/360", headers=tenant_headers(request_id="req-s08-360")
        )
        assert view.status_code == 200
        data = view.json()["data"]
        assert data["identity"]["platform_user_id"] == "u-1"
        assert data["profile"]["display_name"] == "昵称A"
        assert data["preferences"]["theme"] == "dark"
        assert [g["capability_ref"] for g in data["capabilities"]] == ["weather"]
    await store.close()


@pytest.mark.asyncio
async def test_be_s_10_user_360_exposes_all_five_regions() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    async with await _admin_client(store) as client:
        await client.post(
            "/admin/users",
            json={"platform_user_id": "u-2", "display_name": "用户二"},
            headers=tenant_headers(),
        )
        view = await client.get("/admin/users/u-2/360", headers=tenant_headers())
        assert view.status_code == 200
        data = view.json()["data"]
        # 五区：Identity / Profile / Preferences / Capabilities / Policy
        for region in ("identity", "profile", "preferences", "capabilities", "policy"):
            assert region in data, f"缺少区域 {region}"
        assert set(data["identity"]) >= {"platform_user_id", "display_name", "channels"}
        assert data["capabilities"] == [] and data["policy"] == []
    await store.close()


@pytest.mark.asyncio
async def test_be_e_06_unbound_channel_identity_maps_to_user_not_bound() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    async with await _admin_client(store) as client:
        response = await client.get(
            "/admin/users/by-channel",
            params={"channel_type": "web", "channel_user_id": "ghost"},
            headers=tenant_headers(request_id="req-e06"),
        )
        assert response.status_code == 404
        body = response.json()
        assert body["code"] != 0
        assert "user_not_bound" in body["message"]
    await store.close()
