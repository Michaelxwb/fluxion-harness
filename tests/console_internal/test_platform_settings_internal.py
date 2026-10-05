"""[平台设置内部取设置端点] E-04：真实服务身份校验 + 真实租户隔离。

真实边界（业务路径不 mock）：经 `ASGITransport(app=app)` 走真实路由，`require_service_identity`
校验真实 `X-Internal-Service`；租户由 `X-Tenant-Id` 声明，非本租户读不到本租户数据。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_contracts.platform_settings import default_platform_settings
from sqlalchemy import text

from console_internal.conftest import TenantContext
from tests.internal_service import internal_service_token, service_headers  # noqa: F401  (fixture 注册)

URL = "/internal/v1/platform-settings"


@pytest.fixture
async def seeded_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """给本租户植入一个非默认版本（revision 1, task.max_attempts=9），用例结束清理。"""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.platform_setting (tenant_id, revision, settings_json) "
                "VALUES (:tenant_id, 1, CAST(:document AS jsonb))"
            ),
            {"tenant_id": tenant.tenant_id, "document": '{"task": {"max_attempts": 9}}'},
        )
        await session.commit()
    try:
        yield tenant
    finally:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.tenant_id},
            )
            await session.commit()


async def test_service_identity_and_tenant_isolation(
    client: AsyncClient, seeded_tenant: TenantContext
) -> None:
    """[E-04] 无/错服务身份 ⇒ 403；错租户 ⇒ 无数据（不泄漏本租户内容）。"""
    tenant_id = seeded_tenant.tenant_id

    missing = await client.get(URL, headers={"X-Tenant-Id": tenant_id})
    assert missing.status_code == 403
    assert missing.json()["code"] == "FORBIDDEN"
    assert missing.json().get("data") is None

    wrong = await client.get(
        URL, headers={"X-Tenant-Id": tenant_id, "X-Internal-Service": "not-the-token"}
    )
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "FORBIDDEN"

    ok_response = await client.get(URL, headers=service_headers(tenant_id))
    assert ok_response.status_code == 200, ok_response.text
    data = ok_response.json()["data"]
    assert data["revision"] == 1
    assert data["settings"]["task"]["max_attempts"] == 9

    # 非本租户：读到的是该租户（无记录）的 schema 默认，而非上一租户内容
    other = await client.get(URL, headers=service_headers(seeded_tenant.other_tenant_id))
    assert other.status_code == 200
    other_data = other.json()["data"]
    assert other_data["revision"] == 0
    assert other_data["settings"] == asdict(default_platform_settings())
    assert other_data["settings"]["task"]["max_attempts"] == 3
