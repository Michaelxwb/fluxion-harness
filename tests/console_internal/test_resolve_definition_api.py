import uuid

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
        "secret_ref": tenant.model_secret_ref,
        "params": tenant.model_params,
    }
    assert data["skills"] == []
    assert data["mcp_servers"] == []
    secret_fields = [key for key in data["model"] if "secret" in key]
    assert secret_fields == ["secret_ref"]


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
