import uuid
from typing import Any

from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers


def _payload(key: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "key": key or f"model-{uuid.uuid4().hex[:8]}",
        "name": "GPT Test",
        "base_url": "https://api.example.com/v1",
        "model_id": "gpt-4o-mini",
        "api_key": "sk-plaintext-e2e",
        "params": {"temperature": 0.3},
    }
    body.update(overrides)
    return body


async def test_s01_create_persists_plaintext_key_without_exposing_it(
    client: AsyncClient, tenant: TenantContext
) -> None:
    key = f"model-{uuid.uuid4().hex[:8]}"
    created = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    assert created.status_code == 200
    model = created.json()["data"]
    assert model["key"] == key
    assert model["api_key_configured"] is True
    assert model["revision"] == 1
    assert model["last_test_status"] == "UNTESTED"
    assert "api_key" not in model

    listing = await client.get(
        "/api/v1/models", params={"keyword": key}, headers=_headers(tenant)
    )
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["api_key_configured"] is True
    assert "api_key" not in items[0]

    detail = await client.get(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert "api_key" not in detail.json()["data"]

    session_factory = get_session_factory()
    async with session_factory() as session:
        api_key = await session.scalar(
            text("SELECT api_key FROM control.model_definition WHERE id = :id"),
            {"id": uuid.UUID(model["id"])},
        )
    assert api_key == "sk-plaintext-e2e"


async def test_s03_disable_and_soft_delete_model(client: AsyncClient, tenant: TenantContext) -> None:
    key = f"model-{uuid.uuid4().hex[:8]}"
    created = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    model = created.json()["data"]

    disabled = await client.put(
        f"/api/v1/models/{model['id']}",
        json={"expected_revision": 1, "enabled": False},
        headers=_headers(tenant),
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False
    assert disabled.json()["data"]["revision"] == 2

    filtered = await client.get(
        "/api/v1/models", params={"enabled": "false"}, headers=_headers(tenant)
    )
    assert key in [item["key"] for item in filtered.json()["data"]["items"]]

    deleted = await client.delete(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert deleted.status_code == 200
    listing = await client.get(
        "/api/v1/models", params={"keyword": key}, headers=_headers(tenant)
    )
    assert listing.json()["data"]["total"] == 0


async def test_e01_stale_revision_conflicts_without_overwrite(
    client: AsyncClient, tenant: TenantContext
) -> None:
    created = await client.post("/api/v1/models", json=_payload(), headers=_headers(tenant))
    model = created.json()["data"]

    first = await client.put(
        f"/api/v1/models/{model['id']}",
        json={"expected_revision": 1, "name": "Renamed"},
        headers=_headers(tenant),
    )
    assert first.status_code == 200
    assert first.json()["data"]["revision"] == 2

    stale = await client.put(
        f"/api/v1/models/{model['id']}",
        json={"expected_revision": 1, "name": "Should Not Apply"},
        headers=_headers(tenant),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"

    detail = await client.get(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert detail.json()["data"]["name"] == "Renamed"


async def test_e03_delete_referenced_model_returns_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    created = await client.post("/api/v1/models", json=_payload(), headers=_headers(tenant))
    model = created.json()["data"]
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Model Ref Agent",
            "instructions": "help",
            "model_id": model["id"],
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    assert agent.status_code == 200

    blocked = await client.delete(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "COMMON_CONFLICT"

    detail = await client.get(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert detail.status_code == 200


async def test_e02_e05_schema_rejects_reserved_and_immutable_fields(
    client: AsyncClient, tenant: TenantContext
) -> None:
    with_default = await client.post(
        "/api/v1/models", json=_payload(is_default=True), headers=_headers(tenant)
    )
    assert with_default.status_code == 422
    assert with_default.json()["code"] == "COMMON_VALIDATION_ERROR"

    non_openai = await client.post(
        "/api/v1/models", json=_payload(protocol="ANTHROPIC"), headers=_headers(tenant)
    )
    assert non_openai.status_code == 422
    assert non_openai.json()["code"] == "COMMON_VALIDATION_ERROR"

    created = await client.post("/api/v1/models", json=_payload(), headers=_headers(tenant))
    model = created.json()["data"]
    for body in (
        {"expected_revision": 1, "protocol": "OPENAI"},
        {"expected_revision": 1, "key": "changed-key"},
    ):
        response = await client.put(
            f"/api/v1/models/{model['id']}", json=body, headers=_headers(tenant)
        )
        assert response.status_code == 422
        assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_duplicate_key_conflict_and_validation(
    client: AsyncClient, tenant: TenantContext
) -> None:
    key = f"model-{uuid.uuid4().hex[:8]}"
    first = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    assert first.status_code == 200
    duplicate = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "COMMON_CONFLICT"

    bad_url = await client.post(
        "/api/v1/models", json=_payload(base_url="ftp://api.example.com"), headers=_headers(tenant)
    )
    assert bad_url.status_code == 422
