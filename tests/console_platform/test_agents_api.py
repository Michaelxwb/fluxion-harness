import uuid

from httpx import AsyncClient

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, key: str) -> dict[str, object]:
    return {
        "key": key,
        "name": "Agent One",
        "description": "demo agent",
        "instructions": "You are helpful.",
        "model_id": str(tenant.model_id),
        "runtime_config": {"temperature": 0.2},
    }


async def test_create_get_and_list_pagination(client: AsyncClient, tenant: TenantContext) -> None:
    created = await client.post(
        "/api/v1/agents", json=_payload(tenant, "agent-alpha"), headers=_headers(tenant)
    )
    assert created.status_code == 200
    body = created.json()
    assert body["code"] == "0"
    agent = body["data"]
    assert agent["revision"] == 1
    assert agent["key"] == "agent-alpha"
    assert agent["enabled"] is True
    assert agent["runtime_config"] == {"temperature": 0.2}
    assert agent["create_time"]
    assert agent["update_time"]

    detail = await client.get(f"/api/v1/agents/{agent['id']}", headers=_headers(tenant))
    assert detail.status_code == 200
    assert detail.json()["data"]["instructions"] == "You are helpful."

    listing = await client.get(
        "/api/v1/agents",
        params={"page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["page"] == 1
    assert page["page_size"] == 20
    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == [agent["id"]]
    assert "instructions" not in page["items"][0]


async def test_create_with_unknown_model_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    payload = _payload(tenant, "agent-unknown-model")
    payload["model_id"] = str(uuid.uuid4())
    response = await client.post("/api/v1/agents", json=payload, headers=_headers(tenant))
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def test_create_with_disabled_model_returns_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    payload = _payload(tenant, "agent-disabled-model")
    payload["model_id"] = str(tenant.disabled_model_id)
    response = await client.post("/api/v1/agents", json=payload, headers=_headers(tenant))
    assert response.status_code == 409
    assert response.json()["code"] == "MODEL_DISABLED"


async def test_create_duplicate_key_returns_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    payload = _payload(tenant, "agent-duplicate")
    first = await client.post("/api/v1/agents", json=payload, headers=_headers(tenant))
    second = await client.post("/api/v1/agents", json=payload, headers=_headers(tenant))
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "COMMON_CONFLICT"


async def test_update_bumps_revision_and_rejects_stale_revision(
    client: AsyncClient, tenant: TenantContext
) -> None:
    created = (
        await client.post("/api/v1/agents", json=_payload(tenant, "agent-update"), headers=_headers(tenant))
    ).json()["data"]
    updated = await client.put(
        f"/api/v1/agents/{created['id']}",
        json={"expected_revision": 1, "name": "Renamed", "enabled": False},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200
    data = updated.json()["data"]
    assert data["name"] == "Renamed"
    assert data["enabled"] is False
    assert data["revision"] == 2
    assert data["instructions"] == "You are helpful."

    stale = await client.put(
        f"/api/v1/agents/{created['id']}",
        json={"expected_revision": 1},
        headers=_headers(tenant),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"

    current = await client.put(
        f"/api/v1/agents/{created['id']}",
        json={"expected_revision": 2},
        headers=_headers(tenant),
    )
    assert current.status_code == 200
    assert current.json()["data"]["revision"] == 3


async def test_update_with_unknown_model_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    created = (
        await client.post(
            "/api/v1/agents",
            json=_payload(tenant, "agent-update-model"),
            headers=_headers(tenant),
        )
    ).json()["data"]
    response = await client.put(
        f"/api/v1/agents/{created['id']}",
        json={"expected_revision": 1, "model_id": str(uuid.uuid4())},
        headers=_headers(tenant),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def test_delete_hides_agent_from_get_and_list(
    client: AsyncClient, tenant: TenantContext
) -> None:
    created = (
        await client.post("/api/v1/agents", json=_payload(tenant, "agent-delete"), headers=_headers(tenant))
    ).json()["data"]
    deleted = await client.delete(f"/api/v1/agents/{created['id']}", headers=_headers(tenant))
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": True}

    missing = await client.get(f"/api/v1/agents/{created['id']}", headers=_headers(tenant))
    assert missing.status_code == 404
    assert missing.json()["code"] == "AGENT_NOT_FOUND"

    page = (await client.get("/api/v1/agents", headers=_headers(tenant))).json()["data"]
    assert page["total"] == 0
    assert page["items"] == []


async def test_unknown_agent_returns_not_found(client: AsyncClient, tenant: TenantContext) -> None:
    response = await client.get(f"/api/v1/agents/{uuid.uuid4()}", headers=_headers(tenant))
    assert response.status_code == 404
    assert response.json()["code"] == "AGENT_NOT_FOUND"


async def test_invalid_payloads_return_validation_envelope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    payload = _payload(tenant, "agent-invalid")
    payload["unexpected"] = True
    extra_field = await client.post("/api/v1/agents", json=payload, headers=_headers(tenant))
    big_page = await client.get(
        "/api/v1/agents", params={"page_size": 1000}, headers=_headers(tenant)
    )
    zero_page = await client.get("/api/v1/agents", params={"page": 0}, headers=_headers(tenant))
    for response in (extra_field, big_page, zero_page):
        assert response.status_code == 422
        assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_b01_list_aggregates_and_filters(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-01 延伸] 列表聚合计数与 keyword/enabled 筛选（真实 HTTP + 真实 PostgreSQL）。"""
    from sqlalchemy import select

    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import (
        AgentDefinition,
        AgentSkillBinding,
        Skill,
    )
    key = f"agent-agg-{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/api/v1/agents", json=_payload(tenant, key), headers=_headers(tenant)
    )
    assert created.status_code == 200
    agent_id = created.json()["data"]["id"]

    async with get_session_factory()() as session:
        row = await session.scalar(
            select(AgentDefinition.id).where(AgentDefinition.key == key)
        )
        skill_id = await session.scalar(select(Skill.id).limit(1))
        assert row is not None
        if skill_id is None:
            tenant_skill = Skill(
                tenant_id=tenant.tenant_id,
                key=f"skill-{uuid.uuid4()}",
                name="agg skill",
                description="d",
            )
            session.add(tenant_skill)
            await session.flush()
            skill_id = tenant_skill.id
        session.add(AgentSkillBinding(agent_id=row, skill_id=skill_id))
        await session.commit()

    listing = await client.get(
        "/api/v1/agents",
        params={"keyword": key},
        headers=_headers(tenant),
    )
    assert listing.status_code == 200
    item = listing.json()["data"]["items"][0]
    assert item["key"] == key
    for field in ("model_name", "skill_count", "mcp_count", "channel_count", "user_count"):
        assert field in item

    enabled_filtered = await client.get(
        "/api/v1/agents",
        params={"enabled": "false"},
        headers=_headers(tenant),
    )
    assert enabled_filtered.status_code == 200
    assert all(item["enabled"] is False for item in enabled_filtered.json()["data"]["items"])
