"""TASK-008 / S-10：审计页经验资源可见、可筛选、可深链。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from console_platform.conftest import TenantContext


async def _seed_audit(
    tenant_id: str,
    action: str,
    *,
    key: str = "exp.audit",
    user_id: str | None = None,
) -> uuid.UUID:
    from muad_console_platform.infrastructure.db import get_session_factory

    audit_id = uuid.uuid4()
    after = {"experience_key": key, "activation_state": "ACTIVE"}
    if user_id:
        after["user_id"] = user_id
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.config_audit_log"
                " (id, tenant_id, actor_user_id, resource_type, resource_id, action,"
                "  before_json, after_json)"
                " VALUES (:id, :tenant, :actor, 'MEMORY_EXPERIENCE', :resource, :action,"
                "  '{}'::jsonb, CAST(:after AS jsonb))"
            ),
            {
                "id": audit_id,
                "tenant": tenant_id,
                "actor": uuid.uuid4(),
                "resource": uuid.uuid4(),
                "action": action,
                "after": __import__("json").dumps(after),
            },
        )
        await session.commit()
    return audit_id


async def test_s10_audit_filter_target_and_deeplink(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = str(uuid.uuid4())
    await _seed_audit(tenant.tenant_id, "RETIRE", user_id=user_id)
    await _seed_audit(tenant.tenant_id, "TRIAL_PROMOTE", key="exp.other")

    response = await client.get(
        "/api/v1/audits",
        params={"resource_type": "MEMORY_EXPERIENCE"},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 2
    retired = next(item for item in items if item["action"] == "RETIRE")
    assert retired["target"] == f"MEMORY_EXPERIENCE/exp.audit / {user_id}"
    assert retired["resource_type"] == "MEMORY_EXPERIENCE"

    filtered = await client.get(
        "/api/v1/audits",
        params={"resource_type": "MEMORY_EXPERIENCE", "action": "RETIRE"},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert [item["action"] for item in filtered.json()["data"]["items"]] == ["RETIRE"]

    detail = await client.get(
        f"/api/v1/audits/{retired['audit_id']}",
        params={"audit_type": "CONFIG"},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert detail.status_code == 200
    after = detail.json()["data"]["after"]
    assert after["experience_key"] == "exp.audit"
    assert after["user_id"] == user_id
