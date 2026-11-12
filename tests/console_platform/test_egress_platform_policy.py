"""Real tenant-scoped PG/HTTP settings-to-platform address projection."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from muad_console_platform.application.egress_policy import current_egress_policy
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ProjectPlatform
from sqlalchemy import delete

from console_platform.conftest import TenantContext
from tests.internal_service import TOKEN


@pytest.fixture
async def egress_platform(tenant: TenantContext) -> AsyncIterator[ProjectPlatform]:
    async with get_session_factory()() as session:
        platform = ProjectPlatform(
            tenant_id=tenant.tenant_id,
            key="assets",
            name="Asset platform",
            resolver_type="BASE_URL",
            resolver_config_json={"base_url": "https://assets.test/gateway/v1"},
            adapter_key="generic-http",
            adapter_config_json={},
            credential_mode="NONE",
            enabled=True,
        )
        session.add(platform)
        await session.commit()
        await session.refresh(platform)
    try:
        yield platform
    finally:
        async with get_session_factory()() as session:
            from muad_console_platform.infrastructure.models.control import PlatformSetting

            await session.execute(
                delete(PlatformSetting).where(PlatformSetting.tenant_id == tenant.tenant_id)
            )
            await session.execute(delete(ProjectPlatform).where(ProjectPlatform.id == platform.id))
            await session.commit()


def settings_payload(platform="assets", **patch):
    rule = {
        "target_instance_id": platform,
        "method": "GET",
        "path_template": "/assets/{id}",
        "window_requests": 50,
        "window_seconds": 60,
        "max_inflight": 4,
        **patch,
    }
    return {"revision": 0, "settings": {"egress": {"enabled": True, "rules_json": json.dumps([rule])}}}


async def test_admin_selects_platform_and_only_relative_path_is_resolved(
    client: AsyncClient,
    tenant: TenantContext,
    egress_platform: ProjectPlatform,
    internal_service_token,
):
    saved = await client.put("/api/v1/platform-settings", json=settings_payload())
    assert saved.status_code == 200, saved.text
    response = await client.get(
        "/internal/v1/egress-policy",
        headers={
            "X-Tenant-Id": tenant.tenant_id,
            "X-Internal-Service": TOKEN,
        },
    )
    assert response.status_code == 200, response.text
    policy = response.json()["data"]
    (rule,) = policy["rules"]
    assert rule["http_match"] == {
        "origin": "https://assets.test",
        "method": "GET",
        "path_template": "/gateway/v1/assets/{id}",
    }
    assert rule["route_id"] == "GET /assets/{id}"
    assert rule["window_requests"] == 50 and rule["max_inflight"] == 4
    async with get_session_factory()() as session:
        assert not (await current_egress_policy(session, "another-tenant")).rules
        platform = await session.get(ProjectPlatform, egress_platform.id)
        platform.resolver_config_json = {"base_url": "https://new.test/api"}
        from datetime import UTC, datetime

        platform.update_time = datetime.now(UTC)
        await session.commit()
        changed = await current_egress_policy(session, tenant.tenant_id)
    assert changed.revision > policy["revision"]
    assert changed.rules[0].http_match.origin == "https://new.test"
    assert changed.rules[0].http_match.path_template == "/api/assets/{id}"
    assert changed.rules[0].id == rule["id"]  # Same counters survive an address change.


@pytest.mark.parametrize(
    "patch",
    [
        {"target_instance_id": "another-tenant-platform"},
        {"path_template": "https://override.test/assets"},
        {"origin": "https://override.test"},
    ],
)
async def test_invalid_or_unknown_platform_rules_cannot_be_saved(
    client: AsyncClient,
    egress_platform: ProjectPlatform,
    patch,
):
    response = await client.put("/api/v1/platform-settings", json=settings_payload(**patch))
    assert response.status_code == 400, response.text


async def test_default_empty_policy_is_unlimited(
    client: AsyncClient, tenant: TenantContext, internal_service_token
):
    response = await client.get(
        "/internal/v1/egress-policy",
        headers={
            "X-Tenant-Id": tenant.tenant_id,
            "X-Internal-Service": TOKEN,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"revision": 0, "rules": []}


async def test_first_use_provisions_domain_but_lost_state_is_never_recreated(tenant: TenantContext):
    import uuid

    from muad_common import SharedSettings
    from muad_platform_sdk.egress_domain import provision_tenant_domain
    from muad_platform_sdk.egress_identity_models import EgressExecution
    from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedRedisMeter
    from redis.asyncio import Redis

    redis = Redis.from_url(SharedSettings().redis_url, decode_responses=True)
    domain = "tenant-bootstrap-" + uuid.uuid4().hex
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    sessions = get_session_factory()
    try:
        await provision_tenant_domain(meter, sessions, tenant.tenant_id)
        assert await redis.hget(meter._key("guard"), "epoch") == "epoch"
        async with sessions() as session:
            session.add(
                EgressExecution(
                    tenant_id=tenant.tenant_id,
                    source_kind="RUN",
                    source_id=uuid.uuid4(),
                    policy_json={},
                    policy_hash="test",
                    meter_epoch="epoch",
                )
            )
            await session.commit()
        await redis.delete(meter._key("guard"))
        with pytest.raises(MeteringUnavailable, match="must not be recreated"):
            await provision_tenant_domain(meter, sessions, tenant.tenant_id)
        assert not await redis.exists(meter._key("guard"))
    finally:
        async with sessions() as session:
            await session.execute(
                delete(EgressExecution).where(EgressExecution.tenant_id == tenant.tenant_id)
            )
            await session.commit()
        keys = [key async for key in redis.scan_iter(meter._key("*"))]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
