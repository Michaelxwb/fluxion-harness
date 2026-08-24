from __future__ import annotations

from pathlib import Path

import pytest
from tests.console_helpers import (
    console_stack,
    create_resource,
    mcp_spec,
    publish_resource,
    tenant_headers,
)

from fluxion.resources import ResourceKind, SubjectType
from fluxion.runtime.resolver import ResourceResolver


@pytest.mark.asyncio
async def test_S_C104_user_binding_is_visible_to_any_runtime_store_instance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bindings.db"
    async with console_stack(db_path=db_path) as writer:
        await create_resource(
            writer.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            spec=mcp_spec(),
            visibility="private",
            request_id="req-S-C104-resource",
        )
        await publish_resource(
            writer.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            request_id="req-S-C104-publish",
        )
        response = await writer.client.post(
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
            headers=tenant_headers(request_id="req-S-C104-binding"),
        )
        assert response.status_code == 200
        assert response.json()["code"] == 0

    async with console_stack(db_path=db_path) as reader:
        resolver = ResourceResolver(reader.store)
        bindings = await resolver.list_bindings(
            tenant_id="tenant-a",
            subject_type=SubjectType.USER,
            subject_id="user-a",
            resource_type=ResourceKind.MCP,
        )
        assert len(bindings) == 1
        assert bindings[0].resource_id == "github"
        assert bindings[0].credential_ref == "secret://tenant-a/users/user-a/github"
