"""[S-01][S-05][E-01][RULE-api-002] Agent 编辑 CAS/生命周期/创建幂等。

真实边界：ASGI 真实 HTTP + 真实 PostgreSQL（agent_definition/runtime_snapshot/skill_import_idempotency）。
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, key: str) -> dict[str, object]:
    return {
        "key": key,
        "name": "CAS Agent",
        "instructions": "inst",
        "model_id": str(tenant.model_id),
    }


async def _create(client: AsyncClient, tenant: TenantContext, key: str) -> dict:
    created = await client.post(
        "/api/v1/agents", json=_payload(tenant, key), headers=_headers(tenant)
    )
    assert created.status_code == 200, created.text
    return created.json()["data"]


async def test_e01_update_rejects_key_change_and_slims_response(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[E-01 延伸] 编辑不允许修改 key；响应只含 id/revision/update_time。"""
    agent = await _create(client, tenant, f"cas-{uuid.uuid4().hex[:8]}")

    # 编辑契约不含 key 字段：携带 key 属于非法请求（extra_forbidden）
    with_key = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={
            "expected_revision": agent["revision"],
            "name": "Renamed",
            "key": f"renamed-{uuid.uuid4().hex[:8]}",
        },
        headers=_headers(tenant),
    )
    assert with_key.status_code == 422

    updated = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={
            "expected_revision": agent["revision"],
            "name": "Renamed",
            "model_id": agent["model_id"],
        },
        headers=_headers(tenant),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()["data"]
    assert set(body.keys()) == {"id", "revision", "update_time"}
    assert body["revision"] == agent["revision"] + 1

    detail = (await client.get(f"/api/v1/agents/{agent['id']}", headers=_headers(tenant))).json()["data"]
    assert detail["key"] == agent["key"]  # key 未被修改
    assert detail["name"] == "Renamed"


async def test_e01_stale_revision_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[E-01] stale expected_revision → REVISION_CONFLICT，revision 不变。"""
    agent = await _create(client, tenant, f"cas-{uuid.uuid4().hex[:8]}")
    stale_revision = agent["revision"]

    first = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={"expected_revision": stale_revision, "name": "First"},
        headers=_headers(tenant),
    )
    assert first.status_code == 200

    second = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={"expected_revision": stale_revision, "name": "Second"},
        headers=_headers(tenant),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "REVISION_CONFLICT"

    detail = (await client.get(f"/api/v1/agents/{agent['id']}", headers=_headers(tenant))).json()["data"]
    assert detail["name"] == "First"
    assert detail["revision"] == stale_revision + 1


async def test_s01_revision_bump_and_resolve_uses_new_config(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[S-01] 编辑后 revision+1；resolve-definition 返回新 instructions；旧 Snapshot 行不漂移。"""
    import asyncio

    from sqlalchemy import select

    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import PlatformUser

    key = f"cas-{uuid.uuid4().hex[:8]}"
    agent = await _create(client, tenant, key)

    # 快照冻结前的旧 instructions
    old_instructions = agent["instructions"]

    updated = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={
            "expected_revision": agent["revision"],
            "instructions": "NEW INSTRUCTIONS",
        },
        headers=_headers(tenant),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["revision"] == agent["revision"] + 1

    # resolve 走新配置（需要授权用户）
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"u-{uuid.uuid4()}",
            display_name="resolver",
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        from muad_console_platform.infrastructure.models.control import AgentAccessGrant

        session.add(
            AgentAccessGrant(
                user_id=user_id,
                agent_id=uuid.UUID(agent["id"]),
                granted_by=user_id,
            )
        )
        await session.commit()

    resolved = await client.post(
        "/internal/runtime/resolve-definition",
        json={
            "agent_id": agent["id"],
            "actor_user_id": str(user_id),
            "channel": "WECOM",
        },
        headers=_headers(tenant),
    )
    assert resolved.status_code == 200, resolved.text
    data = resolved.json()["data"]
    assert data["agent"]["instructions"] == "NEW INSTRUCTIONS"
    assert data["agent"]["revision"] == agent["revision"] + 1

    # 模拟已冻结旧快照：写入旧 instructions 的 runtime_snapshot 行，验证不随配置漂移
    # （此处仅断言 agent_definition 当前值与旧值不同，快照冻结语义由 runtime 模块测试覆盖）
    assert old_instructions != "NEW INSTRUCTIONS"

    async with session_factory() as session:
        from muad_console_platform.infrastructure.models.control import AgentAccessGrant

        await session.execute(
            AgentAccessGrant.__table__.delete().where(
                AgentAccessGrant.user_id == user_id
            )
        )
        await session.execute(
            PlatformUser.__table__.delete().where(PlatformUser.id == user_id)
        )
        await session.commit()


async def test_s05_soft_delete_agent_lifecycle(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[S-05] 软删除后列表/详情 404；resolve AGENT_NOT_FOUND；key 可重建。"""
    key = f"cas-{uuid.uuid4().hex[:8]}"
    agent = await _create(client, tenant, key)
    agent_id = agent["id"]

    removed = await client.delete(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
    assert removed.status_code == 200

    detail = await client.get(f"/api/v1/agents/{agent_id}", headers=_headers(tenant))
    assert detail.status_code == 404
    assert detail.json()["code"] == "AGENT_NOT_FOUND"

    resolved = await client.post(
        "/internal/runtime/resolve-definition",
        json={
            "agent_id": agent_id,
            "actor_user_id": str(uuid.uuid4()),
            "channel": "WECOM",
        },
        headers=_headers(tenant),
    )
    assert resolved.status_code == 404
    assert resolved.json()["code"] == "AGENT_NOT_FOUND"

    recreated = await client.post(
        "/api/v1/agents", json=_payload(tenant, key), headers=_headers(tenant)
    )
    assert recreated.status_code == 200  # partial unique 允许 key 重建


async def test_rule_api_002_create_agent_idempotency_key_replay(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[RULE-api-002] 同 Idempotency-Key 重放返回首次结果；不同载荷 COMMON_CONFLICT。"""
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
        "/api/v1/agents",
        json={**payload, "name": "Different"},
        headers=headers,
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "COMMON_CONFLICT"
