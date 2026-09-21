"""[B-04][RULE-api-001] 审计查询 API（真实 HTTP + 真实 PostgreSQL config_audit_log）。"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def test_b04_audits_envelope_filter_and_order(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-04] 封套/分页/resource_id 过滤/时间倒序/无 Secret 字段。"""
    agent_key = f"audit-{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/api/v1/agents",
        json={
            "key": agent_key,
            "name": "Audit Agent",
            "instructions": "inst",
            "model_id": str(tenant.model_id),
        },
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    agent_id = created.json()["data"]["id"]

    # 再产生一次 UPDATE 审计
    updated = await client.put(
        f"/api/v1/agents/{agent_id}",
        json={"expected_revision": 1, "name": "Audit Renamed"},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200

    response = await client.get(
        "/api/v1/audits",
        params={"resource_id": agent_id, "page": 1, "page_size": 6},
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    envelope = response.json()
    for field in ("code", "msg", "data", "trace_id", "request_id", "timestamp"):
        assert field in envelope
    page = envelope["data"]
    for field in ("items", "page", "page_size", "total"):
        assert field in page
    assert page["total"] == 2  # CREATE + UPDATE
    assert page["page_size"] == 6
    for item in page["items"]:
        for field in (
            "id",
            "resource_type",
            "resource_id",
            "action",
            "create_time",
            "actor_user_id",
            "actor_display_name",
            "result_status",
        ):
            assert field in item, f"缺字段 {field}"
        assert "secret" not in item
        assert item["result_status"] == "SUCCESS"
        assert item["actor_display_name"] == "Console Admin"
    times = [item["create_time"] for item in page["items"]]
    assert times == sorted(times, reverse=True)  # 时间倒序

    # keyword 过滤：命中 action=UPDATE 的一条
    filtered = await client.get(
        "/api/v1/audits",
        params={"resource_id": agent_id, "keyword": "UPDATE"},
        headers=_headers(tenant),
    )
    assert filtered.json()["data"]["total"] == 1
    assert filtered.json()["data"]["items"][0]["action"] == "UPDATE"

    # 边界：page_size 越界 422
    rejected = await client.get(
        "/api/v1/audits", params={"page_size": 101}, headers=_headers(tenant)
    )
    assert rejected.status_code == 422
