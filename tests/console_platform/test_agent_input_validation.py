"""Reject invalid Agent inputs before writes or idempotency fingerprints."""

import json

import pytest
from httpx import AsyncClient

from console_platform.conftest import TenantContext
from console_platform.test_agents_api import _payload


@pytest.mark.parametrize("field", ["name", "instructions", "model_id", "runtime_config", "enabled"])
async def test_agent_update_rejects_null_required_fields(
    client: AsyncClient, tenant: TenantContext, field: str,
) -> None:
    created = await client.post("/api/v1/agents", json=_payload(tenant, "null-update"))
    agent_id = created.json()["data"]["id"]
    response = await client.put(
        f"/api/v1/agents/{agent_id}", json={"expected_revision": 1, field: None},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"
    detail = (await client.get(f"/api/v1/agents/{agent_id}")).json()["data"]
    assert detail["revision"] == 1
    assert detail[field] is not None


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_agent_config_rejects_nested_nonfinite_numbers(
    client: AsyncClient, tenant: TenantContext, number: float, operation: str,
) -> None:
    config = {"nested": [{"threshold": number}]}
    if operation == "create":
        payload = {**_payload(tenant, "nonfinite-create"), "runtime_config": config}
        path, method = "/api/v1/agents", client.post
    else:
        created = await client.post("/api/v1/agents", json=_payload(tenant, "nonfinite-update"))
        path, method = f"/api/v1/agents/{created.json()['data']['id']}", client.put
        payload = {"expected_revision": 1, "runtime_config": config}
    response = await method(
        path, content=json.dumps(payload),
        headers={"Content-Type": "application/json", "Idempotency-Key": "nonfinite-config"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_agent_update_allows_description_null_and_omitted_fields(
    client: AsyncClient, tenant: TenantContext,
) -> None:
    config = {"nested": [{"threshold": 0.5, "optional": None, "enabled": False}]}
    payload = {**_payload(tenant, "partial-update"), "description": "old", "runtime_config": config}
    created = await client.post("/api/v1/agents", json=payload)
    agent_id = created.json()["data"]["id"]
    response = await client.put(
        f"/api/v1/agents/{agent_id}", json={"expected_revision": 1, "description": None},
    )
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == 2
    detail = (await client.get(f"/api/v1/agents/{agent_id}")).json()["data"]
    assert detail["description"] is None
    assert detail["runtime_config"] == config
    assert detail["instructions"] == payload["instructions"]
    assert detail["revision"] == 2
