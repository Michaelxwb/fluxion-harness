from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from httpx import AsyncClient
from muad_agent_worker.api.deps import get_tenant_id
from muad_agent_worker.main import app

from agent_worker.conftest import TenantContext
from agent_worker.helpers import create_task_payload, internal_service_headers


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


#: v2 的写入口：创建/修改已由 Draft+确认取代（API-02/03 退役），这里覆盖动作端点。
SCHEDULE_WRITE_CALLS = [
    ("post", "/internal/schedules/{schedule_id}/pause"),
    ("post", "/internal/schedules/{schedule_id}/resume"),
    ("post", "/internal/schedules/{schedule_id}/cancel"),
]


@pytest.mark.parametrize(("method", "path"), SCHEDULE_WRITE_CALLS)
async def test_schedule_writes_reject_tenant_header_mismatch(
    client: AsyncClient,
    tenant: TenantContext,
    method: str,
    path: str,
) -> None:
    request_path = path.format(schedule_id=uuid.uuid4())
    with tenant_override("tenant-a"):
        response = await getattr(client, method)(
            request_path,
            headers=internal_service_headers("tenant-b", **{"X-Actor-User-Id": str(uuid.uuid4())}),
            json={"reason": "USER_REQUEST"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "COMMON_BAD_REQUEST"


async def test_schedule_write_accepts_matching_tenant_header(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """租户一致时守卫放行：不存在的 Schedule 由业务层给 404，而不是 400/403。"""
    path = f"/internal/schedules/{uuid.uuid4()}/pause"
    with tenant_override(tenant.tenant_id):
        response = await client.post(
            path,
            json={"reason": "USER_REQUEST"},
            headers=internal_service_headers(
                tenant.tenant_id,
                **{
                    "X-Actor-User-Id": str(uuid.uuid4()),
                    "Idempotency-Key": f"guard-{uuid.uuid4()}",
                },
            ),
        )

    assert response.status_code == 404
    assert response.json()["code"] == "SCHEDULE_NOT_FOUND"


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
