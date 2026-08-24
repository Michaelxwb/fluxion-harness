from __future__ import annotations

import pytest
from tests.console_helpers import (
    console_stack,
    create_resource,
    mcp_spec,
    publish_resource,
    tenant_headers,
)

from fluxion.resources import ResourceKind


@pytest.mark.asyncio
async def test_E_C102_cross_tenant_private_resource_binding_is_rejected() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            tenant_id="tenant-b",
            actor_id="admin-b",
            visibility="private",
            spec=mcp_spec(),
            request_id="req-E-C102-resource",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            tenant_id="tenant-b",
            actor_id="admin-b",
            request_id="req-E-C102-publish",
        )

        response = await stack.client.post(
            "/api/v1/bindings",
            json={
                "subject_type": "user",
                "subject_id": "user-a",
                "resource_type": "mcp",
                "resource_id": "github",
                "version_selector": "latest-published",
                "credential_ref": "secret://tenant-a/users/user-a/github",
                "config": {"enabled_tools": ["list_pr"]},
            },
            headers=tenant_headers(tenant_id="tenant-a", request_id="req-E-C102-binding"),
        )

        payload = response.json()
        assert response.status_code in {403, 404}
        assert payload["code"] != 0
        assert payload["data"] is None
        assert "tenant-b" not in response.text
        bindings = await stack.store.list_bindings(
            subject_type="user",
            subject_id="user-a",
            tenant_id="tenant-a",
            resource_type=ResourceKind.MCP,
        )
        assert bindings == []
