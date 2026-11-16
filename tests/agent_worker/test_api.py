from __future__ import annotations

import json
import uuid

from httpx import AsyncClient

from agent_worker.conftest import TenantContext
from agent_worker.helpers import (
    count_tasks,
    create_task_payload,
    internal_service_headers,
)


def _headers(tenant: TenantContext) -> dict[str, str]:
    return internal_service_headers(tenant.tenant_id)


async def test_task_api_create_get_list_cancel(client: AsyncClient, tenant: TenantContext) -> None:
    payload = create_task_payload(tenant, idempotency_key="api-task").model_dump(mode="json")
    created = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["status"] == "QUEUED"
    task_id = data["task_id"]

    detail = await client.get(f"/internal/tasks/{task_id}", headers=_headers(tenant))
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["task_id"] == task_id
    assert body["status"] == "QUEUED"
    assert body["task_type"] == "SKILL"
    assert body["skill_execution_mode"] == "ASYNC"
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
    invalid = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_non_standard_json_payload_is_rejected_at_the_boundary(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """非 JSON 的值（NaN）必须在**请求边界**被拒，而不是落到 jsonb 列才炸（2026-10-07）。

    Python 的 `json` 默认既接受也生成 `NaN`/`Infinity`（JSON 规范里没有这两个字面量），所以它
    能一路穿过 DTO 校验与幂等指纹，直到 `INSERT ... input_json::jsonb` 才被 PostgreSQL 拒掉
    （实测 `invalid input syntax for type json`，`Token "NaN" is invalid`）——用户拿到的是
    **500**，而这是调用方送错了载荷，该拿 422。

    同一条规则也管幂等键：默认序列化会把不同的 NaN 都写成 `"NaN"`，两份语义不同的载荷会命中
    同一个指纹（见 `muad_contracts.canonical`）。
    """
    payload = create_task_payload(tenant, idempotency_key="non-json-nan").model_dump(mode="json")
    payload["input"] = {"threshold": "NAN_PLACEHOLDER"}
    raw = json.dumps(payload, ensure_ascii=False).replace('"NAN_PLACEHOLDER"', "NaN")

    response = await client.post(
        "/internal/tasks",
        content=raw.encode("utf-8"),
        headers={**internal_service_headers(tenant.tenant_id), "Content-Type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await count_tasks(tenant, idempotency_key="non-json-nan") == 0, "非法载荷不得落库"
