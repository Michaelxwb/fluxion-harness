"""创建类 POST 的提交指纹校验与首次响应重放（E-07 / B-106 / RULE-api-002）。

真实边界：真实 HTTP handler（ASGI）→ task.task_submission partial unique → 真实 PG。
不 mock 数据库，也不以单次顺序调用冒充并发。
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from conftest import TenantContext
from helpers import create_task_payload
from httpx import AsyncClient

IDEMPOTENCY_HEADER = "Idempotency-Key"

COUNT_SUBMISSIONS = sa.text(
    "SELECT count(*) FROM task.task_submission WHERE tenant_id = :tenant_id"
)
COUNT_TASKS = sa.text("SELECT count(*) FROM task.task_execution WHERE tenant_id = :tenant_id")


def _headers(tenant: TenantContext, **extra: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id, **extra}


async def _scalar(tenant: TenantContext, statement: sa.TextClause) -> int:
    async with tenant.session_factory() as session:
        return (await session.execute(statement, {"tenant_id": tenant.tenant_id})).scalar()


async def test_same_key_same_payload_replays_first_response(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """同 key 同指纹：重放首次响应，不重复建资源。"""
    payload = create_task_payload(tenant, idempotency_key="replay-1").model_dump(mode="json")

    first = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))
    second = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    assert await _scalar(tenant, COUNT_TASKS) == 1
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


async def test_mismatched_payload_returns_idempotency_mismatch(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """同 key 异指纹：返回 IDEMPOTENCY_MISMATCH，而不是静默复用首次结果。"""
    payload = create_task_payload(tenant, idempotency_key="mismatch-1").model_dump(mode="json")
    accepted = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))
    assert accepted.status_code == 200

    changed = dict(payload)
    changed["intent_key"] = "policy_check_changed"
    conflict = await client.post("/internal/tasks", json=changed, headers=_headers(tenant))

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert await _scalar(tenant, COUNT_TASKS) == 1


async def test_header_key_must_agree_with_body_key(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """Header 与 body 幂等键同时出现时必须一致。"""
    payload = create_task_payload(tenant, idempotency_key="body-key-1").model_dump(mode="json")

    response = await client.post(
        "/internal/tasks",
        json=payload,
        headers=_headers(tenant, **{IDEMPOTENCY_HEADER: "a-different-key"}),
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert await _scalar(tenant, COUNT_TASKS) == 0
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 0


async def test_matching_header_and_body_key_succeeds(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """Header 与 body 一致时正常创建。"""
    payload = create_task_payload(tenant, idempotency_key="agree-1").model_dump(mode="json")

    response = await client.post(
        "/internal/tasks",
        json=payload,
        headers=_headers(tenant, **{IDEMPOTENCY_HEADER: "agree-1"}),
    )

    assert response.status_code == 200, response.text
    assert await _scalar(tenant, COUNT_TASKS) == 1


async def test_failed_request_does_not_reserve_the_key(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """失败请求不占成功记录：被拒的提交不落 submission，同键随后仍可成功。"""
    payload = create_task_payload(tenant, idempotency_key="fail-then-ok").model_dump(mode="json")

    invalid = dict(payload)
    invalid["skill_artifact_id"] = "not-a-uuid"
    rejected = await client.post("/internal/tasks", json=invalid, headers=_headers(tenant))
    assert rejected.status_code == 422
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 0

    accepted = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))
    assert accepted.status_code == 200, accepted.text
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


async def test_concurrent_same_key_creates_one_task(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """并发同 key：只成功建一个 Task，落败者读到首次结果。"""
    payload = create_task_payload(tenant, idempotency_key="concurrent-1").model_dump(mode="json")

    responses = await asyncio.gather(
        *(client.post("/internal/tasks", json=payload, headers=_headers(tenant)) for _ in range(4)),
        return_exceptions=True,
    )

    failures = [item for item in responses if isinstance(item, BaseException)]
    assert not failures, f"并发提交抛异常: {failures!r}"
    accepted = [item for item in responses if item.status_code == 200]
    assert accepted, [item.text for item in responses]

    task_ids = {item.json()["data"]["task_id"] for item in accepted}
    assert len(task_ids) == 1, f"并发同 key 建出多个 Task: {task_ids}"
    assert await _scalar(tenant, COUNT_TASKS) == 1
    assert await _scalar(tenant, COUNT_SUBMISSIONS) == 1


async def test_e07_replay_and_mismatch_on_real_pg(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """E-07：同 key 同指纹重放首次持久化结果；异指纹 IDEMPOTENCY_MISMATCH；并发只成功一次。"""
    payload = create_task_payload(tenant, idempotency_key="e07-key").model_dump(mode="json")

    first = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))
    assert first.status_code == 200
    replayed = await client.post("/internal/tasks", json=payload, headers=_headers(tenant))
    assert replayed.json()["data"] == first.json()["data"]
    assert await _scalar(tenant, COUNT_TASKS) == 1

    changed = dict(payload)
    changed["input"] = {"customers": ["Z"]}
    mismatch = await client.post("/internal/tasks", json=changed, headers=_headers(tenant))
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"

    other = create_task_payload(tenant, idempotency_key="e07-concurrent").model_dump(mode="json")
    responses = await asyncio.gather(
        *(client.post("/internal/tasks", json=other, headers=_headers(tenant)) for _ in range(3)),
        return_exceptions=True,
    )
    accepted = [item for item in responses if not isinstance(item, BaseException) and item.status_code == 200]
    assert len({item.json()["data"]["task_id"] for item in accepted}) == 1
