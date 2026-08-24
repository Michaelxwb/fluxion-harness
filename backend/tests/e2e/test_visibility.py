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
async def test_B_C102_same_display_name_resolves_by_resource_id_without_ambiguous_override() -> None:
    async with console_stack() as stack:
        resources = [
            ("github-public", "public"),
            ("github-tenant", "tenant"),
            ("github-private", "private"),
        ]
        for resource_id, visibility in resources:
            await create_resource(
                stack.client,
                kind=ResourceKind.MCP,
                resource_id=resource_id,
                visibility=visibility,
                spec=mcp_spec(display_name="github"),
                request_id=f"req-B-C102-create-{resource_id}",
            )
            await publish_resource(
                stack.client,
                kind=ResourceKind.MCP,
                resource_id=resource_id,
                request_id=f"req-B-C102-publish-{resource_id}",
            )

        seen: dict[str, str] = {}
        for resource_id, visibility in resources:
            response = await stack.client.get(
                f"/api/v1/resources/mcp/{resource_id}",
                headers=tenant_headers(request_id=f"req-B-C102-read-{resource_id}"),
            )
            payload = response.json()
            assert response.status_code == 200
            assert payload["code"] == 0
            assert payload["data"]["resource_id"] == resource_id
            assert payload["data"]["visibility"] == visibility
            assert payload["data"]["spec"]["display_name"] == "github"
            seen[resource_id] = payload["data"]["spec"]["display_name"]

        assert seen == {
            "github-public": "github",
            "github-tenant": "github",
            "github-private": "github",
        }
