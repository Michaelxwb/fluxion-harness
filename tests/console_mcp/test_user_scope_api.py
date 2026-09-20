"""[S-02][E-03][B-05] MCP 用户范围与指定用户 API（真实 HTTP + 真实 PostgreSQL）。"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from console_mcp.conftest import create_mcp, tenant_headers


async def _registered_mcp(client: AsyncClient, env: dict[str, object]) -> str:
    created = await create_mcp(client, env, endpoint="http://127.0.0.1:9/mcp")
    assert created.status_code == 200
    return str(created.json()["data"]["mcp_id"])


async def test_s02_user_scope_and_grants(client: AsyncClient, env: dict[str, object]) -> None:
    """[S-02] 范围切换/添加/列表/移除/软删重建；只创建 McpUserGrant。"""
    mcp_id = await _registered_mcp(client, env)

    scope = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}/user-scope",
        json={"user_scope": "ALL"},
        headers=tenant_headers(env),
    )
    assert scope.status_code == 200, scope.text
    assert scope.json()["data"]["user_scope"] == "ALL"

    back = await client.put(
        f"/api/v1/mcp-servers/{mcp_id}/user-scope",
        json={"user_scope": "SELECTED"},
        headers=tenant_headers(env),
    )
    assert back.status_code == 200

    empty = await client.get(f"/api/v1/mcp-servers/{mcp_id}/users", headers=tenant_headers(env))
    assert empty.status_code == 200
    assert empty.json()["data"]["items"] == []
    assert empty.json()["data"]["total"] == 0

    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.control import AgentAccessGrant
    from muad_console_platform.infrastructure.models.mcp import (
        AgentMcpBinding,
    )
    from muad_console_platform.infrastructure.models.mcp import (
        McpUserGrant as GrantModel,
    )
    from sqlalchemy import func, select

    async with get_session_factory()() as session:
        access_before = (
            await session.execute(
                select(func.count()).select_from(AgentAccessGrant).where(
                    AgentAccessGrant.user_id == uuid.UUID(str(env["actor_user_id"]))
                )
            )
        ).scalar_one()
        bindings_before = (
            await session.execute(
                select(func.count()).select_from(AgentMcpBinding).where(
                    AgentMcpBinding.mcp_server_id == uuid.UUID(mcp_id)
                )
            )
        ).scalar_one()

    granted = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/users/{env['actor_user_id']}",
        headers=tenant_headers(env),
    )
    assert granted.status_code == 200, granted.text
    grant = granted.json()["data"]
    assert grant["user_id"] == str(env["actor_user_id"])
    assert grant["display_name"] == "Actor"

    async with get_session_factory()() as session:
        grants = (
            await session.execute(
                select(GrantModel).where(
                    GrantModel.mcp_server_id == uuid.UUID(mcp_id),
                    GrantModel.user_id == uuid.UUID(str(env["actor_user_id"])),
                )
            )
        ).scalars().all()
        assert len(grants) == 1
        assert not hasattr(grants[0], "expires_at")
        access_after = (
            await session.execute(
                select(func.count()).select_from(AgentAccessGrant).where(
                    AgentAccessGrant.user_id == uuid.UUID(str(env["actor_user_id"]))
                )
            )
        ).scalar_one()
        bindings_after = (
            await session.execute(
                select(func.count()).select_from(AgentMcpBinding).where(
                    AgentMcpBinding.mcp_server_id == uuid.UUID(mcp_id)
                )
            )
        ).scalar_one()
    assert access_after == access_before  # 不创建 AgentAccessGrant
    assert bindings_after == bindings_before  # 不创建 AgentMcpBinding

    listed = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/users", headers=tenant_headers(env))
    ).json()["data"]
    assert listed["total"] == 1
    assert [item["user_id"] for item in listed["items"]] == [str(env["actor_user_id"])]

    removed = await client.delete(
        f"/api/v1/mcp-servers/{mcp_id}/users/{env['actor_user_id']}",
        headers=tenant_headers(env),
    )
    assert removed.status_code == 200
    after_remove = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/users", headers=tenant_headers(env))
    ).json()["data"]
    assert after_remove["items"] == []

    again = await client.delete(
        f"/api/v1/mcp-servers/{mcp_id}/users/{env['actor_user_id']}",
        headers=tenant_headers(env),
    )
    assert again.status_code == 404
    assert again.json()["code"] == "COMMON_NOT_FOUND"

    regranted = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/users/{env['actor_user_id']}",
        headers=tenant_headers(env),
    )
    assert regranted.status_code == 200  # 撤销=软删除，可重新创建


async def test_e03_regranting_an_active_grant_is_idempotent(
    client: AsyncClient, env: dict[str, object]
) -> None:
    """[E-03] 重复授权已激活的 Grant → 幂等：返回既有记录，不新增行、不重复写审计。

    与「用户↔Agent 授权」（GrantService.grant）口径一致；此前这里抛 COMMON_CONFLICT。
    """
    from muad_console_platform.infrastructure.db import get_session_factory
    from sqlalchemy import text

    mcp_id = await _registered_mcp(client, env)
    user_id = str(env["actor_user_id"])
    url = f"/api/v1/mcp-servers/{mcp_id}/users/{user_id}"

    first = await client.post(url, headers=tenant_headers(env))
    assert first.status_code == 200

    async def _audit_count() -> int:
        async with get_session_factory()() as session:
            return int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM control.config_audit_log "
                        "WHERE resource_type = 'MCP_SERVER' AND action = 'UPDATE' "
                        "AND resource_id = CAST(:rid AS uuid)"
                    ),
                    {"rid": mcp_id},
                )
                or 0
            )

    audits_after_first = await _audit_count()

    duplicate = await client.post(url, headers=tenant_headers(env))
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["user_id"] == user_id

    async with get_session_factory()() as session:
        active_rows = await session.scalar(
            text(
                "SELECT count(*) FROM control.mcp_user_grant "
                "WHERE mcp_server_id = CAST(:sid AS uuid) AND user_id = CAST(:uid AS uuid) "
                "AND is_deleted = false"
            ),
            {"sid": mcp_id, "uid": user_id},
        )
    assert int(active_rows or 0) == 1, "幂等授权不得新增行"
    assert await _audit_count() == audits_after_first, "幂等授权不得重复写审计"


async def test_b05_paginated_envelope_and_unknown_user(
    client: AsyncClient, env: dict[str, object]
) -> None:
    """[B-05] 分页封套（非裸数组）；未知用户 404；范围切换不清空 Grant。"""
    mcp_id = await _registered_mcp(client, env)
    for user_key in ("actor_user_id", "other_user_id"):
        response = await client.post(
            f"/api/v1/mcp-servers/{mcp_id}/users/{env[user_key]}",
            headers=tenant_headers(env),
        )
        assert response.status_code == 200

    page = (
        await client.get(
            f"/api/v1/mcp-servers/{mcp_id}/users?page=1&page_size=1",
            headers=tenant_headers(env),
        )
    ).json()["data"]
    for field in ("items", "page", "page_size", "total"):
        assert field in page
    assert page["total"] == 2
    assert len(page["items"]) == 1

    await client.put(
        f"/api/v1/mcp-servers/{mcp_id}/user-scope",
        json={"user_scope": "ALL"},
        headers=tenant_headers(env),
    )
    still = (
        await client.get(f"/api/v1/mcp-servers/{mcp_id}/users", headers=tenant_headers(env))
    ).json()["data"]
    assert still["total"] == 2  # 切换 ALL 不清空既有 Grant

    unknown = await client.post(
        f"/api/v1/mcp-servers/{mcp_id}/users/{uuid.uuid4()}",
        headers=tenant_headers(env),
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "COMMON_NOT_FOUND"
