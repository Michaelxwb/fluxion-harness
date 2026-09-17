from __future__ import annotations

import uuid

from conftest import TenantContext
from helpers import create_schedule_payload, create_task_payload
from httpx import AsyncClient


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def test_task_api_create_get_list_cancel(client: AsyncClient, tenant: TenantContext) -> None:
    payload = create_task_payload(tenant, idempotency_key="api-task").model_dump(mode="json")
    created = await client.post("/internal/tasks", json=payload)
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["status"] == "QUEUED"
    task_id = data["task_id"]

    detail = await client.get(f"/internal/tasks/{task_id}", headers=_headers(tenant))
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["task_id"] == task_id
    assert body["status"] == "QUEUED"
    assert body["delivery_status"] == "PENDING"
    assert body["trigger_type"] == "IMMEDIATE"

    listing = await client.get(
        "/internal/tasks",
        params={"status": "QUEUED", "page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["task_id"] == task_id

    cancelled = await client.post(f"/internal/tasks/{task_id}/cancel", headers=_headers(tenant))
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "CANCELLED"

    terminal = await client.post(f"/internal/tasks/{task_id}/cancel", headers=_headers(tenant))
    assert terminal.json()["data"]["status"] == "CANCELLED"


async def test_task_api_rejects_unknown_and_invalid(client: AsyncClient, tenant: TenantContext) -> None:
    missing = await client.get(f"/internal/tasks/{uuid.uuid4()}", headers=_headers(tenant))
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"

    payload = create_task_payload(tenant, idempotency_key="api-invalid").model_dump(mode="json")
    payload["unexpected"] = True
    invalid = await client.post("/internal/tasks", json=payload)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_schedule_api_lifecycle(client: AsyncClient, tenant: TenantContext) -> None:
    actor_id = uuid.uuid4()
    payload = create_schedule_payload(tenant, actor_user_id=actor_id).model_dump(mode="json")
    created = await client.post(
        "/internal/schedules",
        json=payload,
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    body = created.json()["data"]
    schedule_id = body["schedule_id"]
    assert body["status"] == "ACTIVE"
    assert body["next_fire_at"] is not None

    listing = await client.get(
        "/internal/schedules",
        params={"actor_user_id": str(actor_id)},
        headers=_headers(tenant),
    )
    assert listing.status_code == 200
    assert [item["schedule_id"] for item in listing.json()["data"]] == [schedule_id]

    paused = await client.put(
        f"/internal/schedules/{schedule_id}/pause",
        headers=_headers(tenant),
    )
    assert paused.json()["data"]["status"] == "PAUSED"

    resumed = await client.put(
        f"/internal/schedules/{schedule_id}/resume",
        headers=_headers(tenant),
    )
    assert resumed.json()["data"]["status"] == "ACTIVE"

    deleted = await client.delete(
        f"/internal/schedules/{schedule_id}",
        headers=_headers(tenant),
    )
    assert deleted.json()["data"] == {"deleted": True}

    empty = await client.get("/internal/schedules", headers=_headers(tenant))
    assert empty.json()["data"] == []
