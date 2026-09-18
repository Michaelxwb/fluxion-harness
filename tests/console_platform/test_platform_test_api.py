from __future__ import annotations

import socket
import uuid
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import PlatformUser
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers


class _SilentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        del args


@pytest.fixture
def reachable_server() -> AsyncIterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SilentHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return port


@pytest.fixture
async def platform_bundle(
    client: AsyncClient, tenant: TenantContext, reachable_server: int
) -> AsyncIterator[dict[str, Any]]:
    created = await client.post(
        "/api/v1/project-platforms",
        json={
            "key": f"platform-{uuid.uuid4().hex[:8]}",
            "name": "Probe Platform",
            "resolver_type": "BASE_URL",
            "resolver_config": {"base_url": f"http://127.0.0.1:{reachable_server}"},
            "adapter_key": "generic-http",
            "adapter_config": {"auth_scheme": "bearer"},
            "credential_mode": "USER_ONLY",
            "enabled": True,
        },
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    platform_id = created.json()["data"]["platform_id"]
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"code-{uuid.uuid4()}",
            display_name="Probe User",
        )
        session.add(user)
        await session.commit()
        user_id = str(user.id)
    try:
        yield {"platform_id": platform_id, "user_id": user_id}
    finally:
        async with session_factory() as session:
            for statement in (
                "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.shared_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id",
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
            ):
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


async def test_s03_probe_reports_config_connectivity_and_credential_status(
    client: AsyncClient, tenant: TenantContext, platform_bundle: dict[str, Any]
) -> None:
    platform_id, user_id = platform_bundle["platform_id"], platform_bundle["user_id"]
    url = f"/api/v1/project-platforms/{platform_id}/test"

    unchecked = await client.post(url, json={}, headers=_headers(tenant))
    assert unchecked.status_code == 200
    body = unchecked.json()["data"]
    assert body["config_valid"] is True
    assert body["connectivity"] == "REACHABLE"
    assert body["credential_ref_status"] == "NOT_CHECKED"
    assert body["adapter_key"] == "generic-http"
    assert "checked_at" in body

    await client.put(
        f"/api/v1/project-platforms/{platform_id}/users/{user_id}/credential",
        json={"token": "probe-token"},
        headers=_headers(tenant),
    )
    checked = await client.post(url, json={"test_user_id": user_id}, headers=_headers(tenant))
    assert checked.status_code == 200
    assert checked.json()["data"]["credential_ref_status"] == "ACTIVE"
    assert "probe-token" not in checked.text


async def test_s03_unreachable_host_reports_unreachable_without_session(
    client: AsyncClient, tenant: TenantContext, platform_bundle: dict[str, Any]
) -> None:
    platform_id = platform_bundle["platform_id"]
    updated = await client.put(
        f"/api/v1/project-platforms/{platform_id}",
        json={"resolver_config": {"base_url": f"http://127.0.0.1:{_closed_port()}"}},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200
    probed = await client.post(
        f"/api/v1/project-platforms/{platform_id}/test",
        json={"timeout_ms": 500},
        headers=_headers(tenant),
    )
    assert probed.status_code == 200
    body = probed.json()["data"]
    assert body["connectivity"] == "UNREACHABLE"
    assert body["config_valid"] is False
    assert body["details"]["error"]
    assert "session" not in probed.text.lower()


async def test_e02_missing_credential_returns_credential_missing(
    client: AsyncClient, tenant: TenantContext, platform_bundle: dict[str, Any]
) -> None:
    platform_id, user_id = platform_bundle["platform_id"], platform_bundle["user_id"]
    response = await client.post(
        f"/api/v1/project-platforms/{platform_id}/test",
        json={"test_user_id": user_id},
        headers=_headers(tenant),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CREDENTIAL_MISSING"
