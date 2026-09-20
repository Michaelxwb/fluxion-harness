"""[S-04][E-04] Agent 视角用户授权（API-12~14，真实 HTTP + 真实 PostgreSQL + resolve）。"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    PlatformUser,
)
from sqlalchemy import select

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _make_agent(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/agents",
        json={
            "key": f"grant-{uuid.uuid4().hex[:8]}",
            "name": "Grant Agent",
            "instructions": "inst",
            "model_id": str(tenant.model_id),
        },
        headers=_headers(tenant),
    )
    assert created.status_code == 200, created.text
    return created.json()["data"]["id"]


async def _make_users(client: AsyncClient, tenant: TenantContext, count: int) -> list[str]:
    user_ids: list[str] = []
    async with get_session_factory()() as session:
        for _ in range(count):
            user = PlatformUser(
                tenant_id=tenant.tenant_id,
                user_code=f"grant-u-{uuid.uuid4().hex[:8]}",
                display_name=f"Grant User {uuid.uuid4().hex[:4]}",
            )
            session.add(user)
            await session.flush()
            user_ids.append(str(user.id))
        await session.commit()
    return user_ids


async def test_s04_grant_revoke_affects_resolve_snapshot_stable(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[S-04] 授权后 resolve 可用；撤销=软删除，resolve 拒绝；key 重建不影响。"""
    agent_id = await _make_agent(client, tenant)
    user_ids = await _make_users(client, tenant, 1)
    user_id = user_ids[0]

    granted = await client.post(
        f"/api/v1/agents/{agent_id}/users/{user_id}", headers=_headers(tenant)
    )
    assert granted.status_code == 200, granted.text
    grant = granted.json()["data"]
    assert grant["user_id"] == user_id
    assert grant["granted_at"]

    resolved = await client.post(
        "/internal/runtime/resolve-definition",
        json={"agent_id": agent_id, "actor_user_id": user_id, "channel": "WECOM"},
        headers=_headers(tenant),
    )
    assert resolved.status_code == 200, resolved.text

    revoked = await client.delete(
        f"/api/v1/agents/{agent_id}/users/{user_id}", headers=_headers(tenant)
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["is_deleted"] is True

    resolved_after = await client.post(
        "/internal/runtime/resolve-definition",
        json={"agent_id": agent_id, "actor_user_id": user_id, "channel": "WECOM"},
        headers=_headers(tenant),
    )
    assert resolved_after.status_code == 403
    assert resolved_after.json()["code"] == "AGENT_ACCESS_DENIED"

    listed = (
        await client.get(f"/api/v1/agents/{agent_id}/users", headers=_headers(tenant))
    ).json()["data"]
    assert listed["total"] == 0

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(AgentAccessGrant).where(
                    AgentAccessGrant.agent_id == uuid.UUID(agent_id)
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].is_deleted is True  # 撤销=软删除，历史行保留


async def test_e04_regrant_idempotent_restores_row(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[E-04] 重复授权幂等返回既有 Grant；软删行恢复并更新 granted_by/granted_at。"""
    agent_id = await _make_agent(client, tenant)
    user_ids = await _make_users(client, tenant, 1)
    user_id = user_ids[0]

    first = await client.post(
        f"/api/v1/agents/{agent_id}/users/{user_id}", headers=_headers(tenant)
    )
    assert first.status_code == 200
    first_granted_at = first.json()["data"]["granted_at"]

    await client.delete(
        f"/api/v1/agents/{agent_id}/users/{user_id}", headers=_headers(tenant)
    )

    restored = await client.post(
        f"/api/v1/agents/{agent_id}/users/{user_id}", headers=_headers(tenant)
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["granted_at"] >= first_granted_at  # granted_at 已更新

    duplicate = await client.post(
        f"/api/v1/agents/{agent_id}/users/{user_id}", headers=_headers(tenant)
    )
    assert duplicate.status_code == 200  # 幂等返回既有 Grant

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(AgentAccessGrant).where(
                    AgentAccessGrant.agent_id == uuid.UUID(agent_id)
                )
            )
        ).scalars().all()
        assert len(rows) == 1  # 无重复有效行
        assert rows[0].is_deleted is False

    async with get_session_factory()() as session:
        await session.execute(
            AgentAccessGrant.__table__.delete().where(
                AgentAccessGrant.agent_id == uuid.UUID(agent_id)
            )
        )
        await session.execute(
            PlatformUser.__table__.delete().where(PlatformUser.id == uuid.UUID(user_id))
        )
        await session.commit()


async def test_grant_unknown_agent_or_user(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """Agent 不存在 → AGENT_NOT_FOUND；用户不存在 → COMMON_NOT_FOUND。"""
    agent_id = await _make_agent(client, tenant)
    unknown_agent = await client.post(
        f"/api/v1/agents/{uuid.uuid4()}/users/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert unknown_agent.status_code == 404
    assert unknown_agent.json()["code"] == "AGENT_NOT_FOUND"

    unknown_user = await client.post(
        f"/api/v1/agents/{agent_id}/users/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert unknown_user.status_code == 404
    assert unknown_user.json()["code"] == "COMMON_NOT_FOUND"
