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
async def test_E_C103_credential_ref_metadata_never_accepts_or_echoes_plaintext_secret() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            spec=mcp_spec(),
            request_id="req-E-C103-resource",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            request_id="req-E-C103-publish",
        )

        bad = await stack.client.post(
            "/api/v1/bindings",
            json={
                "subject_type": "user",
                "subject_id": "user-a",
                "resource_type": "mcp",
                "resource_id": "github",
                "version_selector": "latest-published",
                "credential_ref": "github-token-raw",
                "config": {"api_key": "github-token-raw"},
            },
            headers=tenant_headers(request_id="req-E-C103-bad"),
        )
        assert bad.status_code == 400
        assert "github-token-raw" not in bad.text

        good = await stack.client.post(
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
            headers=tenant_headers(request_id="req-E-C103-good"),
        )

        payload = good.json()
        assert good.status_code == 200
        assert payload["code"] == 0
        assert payload["data"]["credential_ref"] == "secret://tenant-a/users/user-a/github"
        assert "github-token-raw" not in good.text
