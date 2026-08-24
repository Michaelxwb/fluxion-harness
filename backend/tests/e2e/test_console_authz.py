from __future__ import annotations

import pytest
from tests.console_helpers import console_stack, create_resource, publish_resource, tenant_headers

from fluxion.resources import ResourceKind


@pytest.mark.asyncio
async def test_E_C105_private_resource_from_another_tenant_is_not_disclosed() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="tenant-b-assistant",
            tenant_id="tenant-b",
            actor_id="admin-b",
            visibility="private",
            spec={"prompt": "tenant-b-private-prompt", "model_policy": {"provider": "dev.echo"}},
            request_id="req-E-C105-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="tenant-b-assistant",
            tenant_id="tenant-b",
            actor_id="admin-b",
            request_id="req-E-C105-publish",
        )

        response = await stack.client.get(
            "/api/v1/resources/runtime_profile/tenant-b-assistant",
            headers=tenant_headers(tenant_id="tenant-a", request_id="req-E-C105-read"),
        )

        payload = response.json()
        assert response.status_code in {403, 404}
        assert payload["code"] != 0
        assert payload["data"] is None
        assert "tenant-b-private-prompt" not in response.text
        assert "tenant-b" not in response.text
