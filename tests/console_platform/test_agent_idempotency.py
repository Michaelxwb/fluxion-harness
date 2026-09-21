"""[RULE-api-002] Agent 创建幂等：同 key 重放/不同指纹冲突/并发串行化。"""

from __future__ import annotations

import asyncio
import uuid

from httpx import AsyncClient

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, key: str) -> dict[str, object]:
    return {
        "key": key,
        "name": "Idempotent Agent",
        "instructions": "inst",
        "model_id": str(tenant.model_id),
    }


async def test_replay_same_key_returns_first_result(
    client: AsyncClient, tenant: TenantContext
) -> None:
    key = f"idem-{uuid.uuid4().hex[:8]}"
    payload = _payload(tenant, key)
    headers = {**_headers(tenant), "Idempotency-Key": f"agent-{uuid.uuid4()}"}

    first = await client.post("/api/v1/agents", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    agent_id = first.json()["data"]["id"]

    replay = await client.post("/api/v1/agents", json=payload, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["id"] == agent_id

    conflicting = await client.post(
        "/api/v1/agents", json={**payload, "name": "Different"}, headers=headers
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "IDEMPOTENCY_MISMATCH"

    listing = await client.get(
        "/api/v1/agents", params={"keyword": key}, headers=_headers(tenant)
    )
    assert listing.json()["data"]["total"] == 1


async def test_concurrent_same_key_creates_single_agent(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """并发同 key：advisory lock 串行化，后到者重放首次结果，只创建一个 Agent。"""
    key = f"idem-{uuid.uuid4().hex[:8]}"
    payload = _payload(tenant, key)
    headers = {**_headers(tenant), "Idempotency-Key": f"agent-{uuid.uuid4()}"}

    async def submit() -> tuple[int, dict[str, object]]:
        response = await client.post("/api/v1/agents", json=payload, headers=headers)
        return response.status_code, response.json()

    results = await asyncio.gather(submit(), submit())
    assert [status for status, _ in results] == [200, 200]
    assert results[0][1]["data"]["id"] == results[1][1]["data"]["id"]

    listing = await client.get(
        "/api/v1/agents", params={"keyword": key}, headers=_headers(tenant)
    )
    assert listing.json()["data"]["total"] == 1
