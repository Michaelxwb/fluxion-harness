from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from conftest import TenantContext
from helpers import create_schedule_payload, create_task_payload
from httpx import AsyncClient
from muad_agent_worker.api.deps import get_tenant_id
from muad_agent_worker.main import app


@contextmanager
def tenant_override(tenant_id: str) -> Iterator[None]:
    app.dependency_overrides[get_tenant_id] = lambda: tenant_id
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_tenant_id, None)


async def test_create_task_rejects_tenant_header_mismatch(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    payload = create_task_payload(tenant, idempotency_key="tenant-mismatch").model_dump(mode="json")
    response = await client.post(
        "/internal/tasks",
        json=payload,
        headers={"X-Tenant-Id": "other-tenant"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "COMMON_BAD_REQUEST"


async def test_create_task_accepts_matching_tenant_header(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    payload = create_task_payload(tenant, idempotency_key="tenant-match").model_dump(mode="json")
    response = await client.post(
        "/internal/tasks",
        json=payload,
        headers={"X-Tenant-Id": tenant.tenant_id},
    )

    assert response.status_code == 200
    assert response.json()["code"] == "0"


SCHEDULE_WRITE_CALLS = [
    ("post", "/internal/schedules"),
    ("put", "/internal/schedules/{schedule_id}/pause"),
    ("put", "/internal/schedules/{schedule_id}/resume"),
    ("delete", "/internal/schedules/{schedule_id}"),
]


@pytest.mark.parametrize(("method", "path"), SCHEDULE_WRITE_CALLS)
async def test_schedule_writes_reject_tenant_header_mismatch(
    client: AsyncClient,
    tenant: TenantContext,
    method: str,
    path: str,
) -> None:
    payload = create_schedule_payload(tenant).model_dump(mode="json")
    request_path = path.format(schedule_id=uuid.uuid4())
    with tenant_override("tenant-a"):
        response = await getattr(client, method)(
            request_path,
            headers={"X-Tenant-Id": "tenant-b", "X-Actor-User-Id": str(uuid.uuid4())},
            **({"json": payload} if method == "post" else {}),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "COMMON_BAD_REQUEST"


async def test_schedule_write_accepts_matching_tenant_header(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    payload = create_schedule_payload(tenant).model_dump(mode="json")
    with tenant_override(tenant.tenant_id):
        response = await client.post(
            "/internal/schedules",
            json=payload,
            headers={"X-Tenant-Id": tenant.tenant_id},
        )

    assert response.status_code == 200
    assert response.json()["data"]["tenant_id"] == tenant.tenant_id
