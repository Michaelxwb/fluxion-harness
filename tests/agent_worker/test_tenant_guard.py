from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from httpx import AsyncClient
from muad_agent_worker.api.deps import get_tenant_id
from muad_agent_worker.main import app

from agent_worker.conftest import TenantContext
from agent_worker.helpers import (
    create_schedule_payload,
    create_task_payload,
    internal_service_headers,
)


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
        headers=internal_service_headers("other-tenant"),
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
        headers=internal_service_headers(tenant.tenant_id),
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
            headers=internal_service_headers("tenant-b", **{"X-Actor-User-Id": str(uuid.uuid4())}),
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
            headers=internal_service_headers(tenant.tenant_id),
        )

    assert response.status_code == 200
    assert response.json()["data"]["tenant_id"] == tenant.tenant_id


# ---------------------------------------------------- 评审回归（2026-10-06）


async def test_internal_routes_require_the_service_identity(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """普通 Task/Schedule 接口与 Admin 面同一门控（评审 #1）。

    此前只有 `/internal/admin/*` 校验服务身份：任何能连上 Worker 端口、知道目标租户/任务 id
    的调用方，只带一个 `X-Tenant-Id` 就能读、建、取消别人的任务，也能改别人的定时任务。
    """
    payload = create_task_payload(tenant, idempotency_key="unauthorized").model_dump(mode="json")
    tenant_header = {"X-Tenant-Id": tenant.tenant_id}

    anonymous_create = await client.post("/internal/tasks", json=payload, headers=tenant_header)
    assert anonymous_create.status_code == 403
    assert anonymous_create.json()["code"] == "FORBIDDEN"

    forged = await client.post(
        "/internal/tasks",
        json=payload,
        headers={**tenant_header, "X-Internal-Service": "not-the-token"},
    )
    assert forged.status_code == 403

    assert (
        await client.get("/internal/tasks", headers=tenant_header)
    ).status_code == 403
    assert (
        await client.get("/internal/schedules", headers=tenant_header)
    ).status_code == 403
    assert (
        await client.post(
            f"/internal/tasks/{uuid.uuid4()}/cancel", headers=tenant_header
        )
    ).status_code == 403

    authorized = await client.post(
        "/internal/tasks", json=payload, headers=internal_service_headers(tenant.tenant_id)
    )
    assert authorized.status_code == 200
