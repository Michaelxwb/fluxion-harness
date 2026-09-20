"""[B-129] API-08 Resolve Egress Access（真实 HTTP + 真实 PostgreSQL 平台/凭据表）。

服务身份校验、平台解析、credential_mode 选择、DENY、CREDENTIAL_MISSING、
HTTP target allowlist、明文仅返回受信调用方。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    ProjectPlatform,
    SharedCredentialRef,
    UserCredentialRef,
)

from console_internal.conftest import TenantContext

URL = "/internal/runtime/resolve-egress-access"

VALID_EXECUTION_TYPES = {"RUN", "TASK"}
VALID_TARGET_TYPES = {"PLATFORM_SERVICE", "HTTP", "MCP"}


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id, "X-Internal-Service": "test-internal-token"}


def _payload(platform_key: str = "mssw-prod", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "actor_user_id": str(uuid.uuid4()),
        "execution_ref": {"type": "RUN", "id": str(uuid.uuid4())},
        "platform_key": platform_key,
        "target": {"type": "PLATFORM_SERVICE", "service": "customer-service-mgr", "operation": "get_customer"},
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-internal-token")


async def _make_platform(
    tenant: TenantContext,
    *,
    credential_mode: str = "USER_THEN_SHARED",
    enabled: bool = True,
) -> str:
    async with get_session_factory()() as session:
        platform = ProjectPlatform(
            tenant_id=tenant.tenant_id,
            key=f"mssw-{uuid.uuid4().hex[:6]}",
            name="MSS",
            resolver_type="BASE_URL",
            resolver_config_json={"base_url": "https://mssw.internal"},
            adapter_key="generic-http",
            credential_mode=credential_mode,
            enabled=enabled,
        )
        session.add(platform)
        await session.commit()
        return platform.key


async def test_b129_requires_service_identity(client: AsyncClient, tenant: TenantContext) -> None:
    """[B-129] 无/错服务身份 → FORBIDDEN。"""
    key = await _make_platform(tenant)
    missing = await client.post(
        URL, json=_payload(key), headers={"X-Tenant-Id": tenant.tenant_id}
    )
    assert missing.status_code in (401, 403)


async def test_b129_platform_not_found_and_validation(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-129] 平台不存在 → COMMON_NOT_FOUND；非法 target/execution 类型 → 422。"""
    missing_platform = await client.post(
        URL, json=_payload(f"nope-{uuid.uuid4().hex[:6]}"), headers=_headers(tenant)
    )
    assert missing_platform.status_code == 404
    assert missing_platform.json()["code"] == "COMMON_NOT_FOUND"

    bad_target = _payload(await _make_platform(tenant))
    bad_target["target"] = {"type": "SOCKET", "url": "x"}
    response = await client.post(URL, json=bad_target, headers=_headers(tenant))
    assert response.status_code == 422

    bad_exec = _payload(await _make_platform(tenant))
    bad_exec["execution_ref"] = {"type": "SESSION", "id": str(uuid.uuid4())}
    response = await client.post(URL, json=bad_exec, headers=_headers(tenant))
    assert response.status_code == 422


async def test_b129_user_then_shared_fallback(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-129] USER_THEN_SHARED：用户凭据优先，缺失回退共享；平台字段齐备。"""
    key = await _make_platform(tenant, credential_mode="USER_THEN_SHARED")
    async with get_session_factory()() as session:
        platform = (
            await session.execute(
                select(ProjectPlatform).where(ProjectPlatform.key == key)
            )
        ).scalar_one()
        shared = SharedCredentialRef(
            tenant_id=tenant.tenant_id,
            platform_id=platform.id,
            credential_json={"token": "shared-secret"},
            credential_schema_version="1",
            status="ACTIVE",
        )
        session.add(shared)
        await session.commit()
        platform_id = platform.id

    payload = _payload(key)
    # FK：凭据用户必须是真实 platform_user
    async with get_session_factory()() as session:
        from muad_console_platform.infrastructure.models.control import PlatformUser

        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"egress-u-{uuid.uuid4().hex[:8]}",
            display_name="Egress User",
        )
        session.add(user)
        await session.flush()
        payload["actor_user_id"] = str(user.id)
        await session.commit()
        actor_id = user.id

    response = await client.post(URL, json=payload, headers=_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["decision"] == "ALLOW"
    assert data["platform"]["key"] == key
    assert data["platform"]["resolver_config"] == {"base_url": "https://mssw.internal"}
    assert data["platform"]["credential_mode"] == "USER_THEN_SHARED"
    assert data["credential"]["credential_json"] == {"token": "shared-secret"}  # 共享回退

    # 用户凭据写入后优先
    async with get_session_factory()() as session:
        session.add(
            UserCredentialRef(
                tenant_id=tenant.tenant_id,
                user_id=uuid.UUID(str(payload["actor_user_id"])),
                platform_id=platform_id,
                credential_json={"token": "user-secret"},
                credential_schema_version="1",
                status="ACTIVE",
            )
        )
        await session.commit()

    preferred = await client.post(URL, json=payload, headers=_headers(tenant))
    assert preferred.status_code == 200
    assert preferred.json()["data"]["credential"]["credential_json"] == {"token": "user-secret"}

    async with get_session_factory()() as session:
        await session.execute(
            UserCredentialRef.__table__.delete().where(UserCredentialRef.platform_id == platform_id)
        )
        await session.execute(
            SharedCredentialRef.__table__.delete().where(SharedCredentialRef.platform_id == platform_id)
        )
        await session.execute(
            ProjectPlatform.__table__.delete().where(ProjectPlatform.id == platform_id)
        )
        from muad_console_platform.infrastructure.models.control import PlatformUser

        await session.execute(PlatformUser.__table__.delete().where(PlatformUser.id == actor_id))
        await session.commit()


async def test_b129_credential_missing(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-129] USER_ONLY 且无用户凭据 → CREDENTIAL_MISSING。"""
    key = await _make_platform(tenant, credential_mode="USER_ONLY")
    response = await client.post(URL, json=_payload(key), headers=_headers(tenant))
    assert response.status_code in (404, 409, 422)
    assert response.json()["code"] == "CREDENTIAL_MISSING"


async def test_b129_none_mode_no_credential(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-129] NONE 模式：ALLOW 且 credential 为空。"""
    key = await _make_platform(tenant, credential_mode="NONE")
    response = await client.post(URL, json=_payload(key), headers=_headers(tenant))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["decision"] == "ALLOW"
    assert data["credential"] is None


async def test_b129_http_target_allowlist(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-129] HTTP target：平台 resolver 非 allowlist 域 → DENY/FORBIDDEN。"""
    key = await _make_platform(tenant)
    payload = _payload(key)
    payload["target"] = {"type": "HTTP", "url": "https://evil.example.com/api", "method": "GET"}
    response = await client.post(URL, json=payload, headers=_headers(tenant))
    assert response.status_code in (200, 403)
    if response.status_code == 200:
        assert response.json()["data"]["decision"] == "DENY"
    else:
        assert response.json()["code"] == "FORBIDDEN"
