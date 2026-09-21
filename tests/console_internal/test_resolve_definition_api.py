import uuid

import sqlalchemy as sa
from httpx import AsyncClient, Response

from console_internal.conftest import TenantContext

RESOLVE_URL = "/internal/runtime/resolve-definition"


def _headers(tenant: TenantContext, tenant_id: str | None = None) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id or tenant.tenant_id}


def _payload(
    agent_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    channel: str = "WECOM",
) -> dict[str, str]:
    return {
        "agent_id": str(agent_id),
        "actor_user_id": str(actor_user_id),
        "channel": channel,
    }


def _assert_error(response: Response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json()["code"] == code


async def test_resolve_returns_agent_and_model(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.agent_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "0"
    data = body["data"]
    assert data["agent"] == {
        "id": str(tenant.agent_id),
        "key": tenant.agent_key,
        "revision": tenant.agent_revision,
        "instructions": tenant.agent_instructions,
        "runtime_config": tenant.agent_runtime_config,
    }
    assert data["model"] == {
        "id": str(tenant.model_id),
        "revision": tenant.model_revision,
        "protocol": "OPENAI",
        "model_id": tenant.model_model_id,
        "base_url": tenant.model_base_url,
        "api_key": tenant.model_api_key,
        "params": tenant.model_params,
    }
    assert data["skills"] == []
    assert data["mcp_servers"] == []
    secret_fields = [key for key in data["model"] if "secret" in key or "api_key" in key]
    assert secret_fields == ["api_key"]


async def test_unknown_agent_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(uuid.uuid4(), tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 404, "AGENT_NOT_FOUND")


async def test_soft_deleted_agent_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.deleted_agent_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 404, "AGENT_NOT_FOUND")


async def test_disabled_agent_returns_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.disabled_agent_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 409, "AGENT_DISABLED")


async def test_missing_grant_returns_forbidden(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.ungranted_agent_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 403, "AGENT_ACCESS_DENIED")


async def test_revoked_grant_returns_forbidden(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.revoked_agent_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 403, "AGENT_ACCESS_DENIED")


async def test_other_actor_returns_forbidden(client: AsyncClient, tenant: TenantContext) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.agent_id, tenant.other_actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 403, "AGENT_ACCESS_DENIED")


async def test_agent_with_deleted_model_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.deleted_model_agent_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 404, "COMMON_NOT_FOUND")


async def test_agent_with_disabled_model_returns_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.disabled_model_agent_id, tenant.actor_user_id),
        headers=_headers(tenant),
    )
    _assert_error(response, 409, "MODEL_DISABLED")


async def test_tenant_isolation_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.agent_id, tenant.actor_user_id),
        headers=_headers(tenant, tenant.other_tenant_id),
    )
    _assert_error(response, 404, "AGENT_NOT_FOUND")


async def test_invalid_channel_returns_validation_envelope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        RESOLVE_URL,
        json=_payload(tenant.agent_id, tenant.actor_user_id, channel="SLACK"),
        headers=_headers(tenant),
    )
    _assert_error(response, 422, "COMMON_VALIDATION_ERROR")


async def test_resolve_mcp_servers_effective_formula(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[B-02] EffectiveMcp 全公式（真实 resolve）：ALL/禁用/解绑/软删/SELECTED grant/撤销 分支。"""
    from muad_console_platform.infrastructure.db import get_session_factory
    from muad_console_platform.infrastructure.models.mcp import (
        AgentMcpBinding,
        McpServer,
        McpUserGrant,
    )

    def _server(key: str, *, user_scope: str = "ALL", enabled: bool = True, deleted: bool = False):
        return McpServer(
            tenant_id=tenant.tenant_id,
            key=key,
            name=f"MCP {key}",
            endpoint="http://127.0.0.1:9/mcp",
            user_scope=user_scope,
            enabled=enabled,
            is_deleted=deleted,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        allowed = _server("mcp-allowed")
        disabled = _server("mcp-disabled", enabled=False)
        deleted = _server("mcp-deleted", deleted=True)
        selected = _server("mcp-selected", user_scope="SELECTED")
        revoked_grant = _server("mcp-revoked", user_scope="SELECTED")
        binding_deleted = _server("mcp-binding-deleted")
        grant_later = _server("mcp-grant-later", user_scope="SELECTED")
        session.add_all(
            [allowed, disabled, deleted, selected, revoked_grant, binding_deleted, grant_later]
        )
        await session.flush()
        session.add_all(
            [
                AgentMcpBinding(agent_id=tenant.agent_id, mcp_server_id=allowed.id),
                AgentMcpBinding(agent_id=tenant.agent_id, mcp_server_id=disabled.id),
                AgentMcpBinding(agent_id=tenant.agent_id, mcp_server_id=deleted.id),
                AgentMcpBinding(agent_id=tenant.agent_id, mcp_server_id=selected.id),
                AgentMcpBinding(agent_id=tenant.agent_id, mcp_server_id=revoked_grant.id),
                AgentMcpBinding(
                    agent_id=tenant.agent_id, mcp_server_id=binding_deleted.id, is_deleted=True
                ),
                AgentMcpBinding(agent_id=tenant.agent_id, mcp_server_id=grant_later.id),
                McpUserGrant(
                    mcp_server_id=selected.id,
                    user_id=tenant.actor_user_id,
                    granted_by=tenant.actor_user_id,
                ),
                McpUserGrant(
                    mcp_server_id=revoked_grant.id,
                    user_id=tenant.actor_user_id,
                    granted_by=tenant.actor_user_id,
                    is_deleted=True,
                ),
            ]
        )
        await session.commit()
        server_ids = [
            allowed.id,
            disabled.id,
            deleted.id,
            selected.id,
            revoked_grant.id,
            binding_deleted.id,
            grant_later.id,
        ]

    try:
        response = await client.post(
            RESOLVE_URL,
            json=_payload(tenant.agent_id, tenant.actor_user_id),
            headers=_headers(tenant),
        )
        assert response.status_code == 200, response.text
        keys = {server["key"] for server in response.json()["data"]["mcp_servers"]}
        assert keys == {"mcp-allowed", "mcp-selected"}, keys

        # 授予后 SELECTED 立即可见；撤销后立即不可见
        async with session_factory() as session:
            session.add(
                McpUserGrant(
                    mcp_server_id=grant_later.id,
                    user_id=tenant.actor_user_id,
                    granted_by=tenant.actor_user_id,
                )
            )
            await session.commit()
        granted = await client.post(
            RESOLVE_URL,
            json=_payload(tenant.agent_id, tenant.actor_user_id),
            headers=_headers(tenant),
        )
        assert "mcp-grant-later" in {
            server["key"] for server in granted.json()["data"]["mcp_servers"]
        }

        async with session_factory() as session:
            row = (
                await session.execute(
                    sa.select(McpUserGrant).where(
                        McpUserGrant.mcp_server_id == grant_later.id,
                        McpUserGrant.user_id == tenant.actor_user_id,
                    )
                )
            ).scalar_one()
            row.is_deleted = True
            await session.commit()
        revoked = await client.post(
            RESOLVE_URL,
            json=_payload(tenant.agent_id, tenant.actor_user_id),
            headers=_headers(tenant),
        )
        assert "mcp-grant-later" not in {
            server["key"] for server in revoked.json()["data"]["mcp_servers"]
        }
    finally:
        async with session_factory() as session:
            await session.execute(
                sa.delete(McpUserGrant).where(McpUserGrant.mcp_server_id.in_(server_ids))
            )
            await session.execute(
                sa.delete(AgentMcpBinding).where(AgentMcpBinding.mcp_server_id.in_(server_ids))
            )
            await session.execute(sa.delete(McpServer).where(McpServer.id.in_(server_ids)))
            await session.commit()
