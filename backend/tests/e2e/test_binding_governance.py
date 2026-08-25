from __future__ import annotations

from sqlalchemy import select
from tests.console_helpers import (
    console_stack,
    create_resource,
    mcp_spec,
    publish_resource,
    tenant_headers,
)

from fluxion.registry.schema import audit_logs, outbox_events
from fluxion.resources import ResourceKind


async def test_S_A12_binding_grant_writes_outbox_audit_and_bumps_revision() -> None:
    """A12：binding grant 走 commit_binding 单事务——insert binding + bump_revision
    + audit + outbox 原子化。此前 put_binding + 独立 bump_revision（非原子）且
    不写 outbox（跨 Pod 权限生效延迟）。断言四者同事务落地。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            spec=mcp_spec(),
            request_id="req-S-A12-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.MCP,
            resource_id="github",
            request_id="req-S-A12-publish",
        )
        revision_before = await stack.store.read_revision(tenant_id="tenant-a")

        response = await stack.client.post(
            "/api/v1/bindings",
            json={
                "subject_type": "user",
                "subject_id": "user-a",
                "resource_type": "mcp",
                "resource_id": "github",
                "version_selector": "latest-published",
                "config": {"enabled_tools": ["list_pr"]},
            },
            headers=tenant_headers(request_id="req-S-A12-binding"),
        )
        assert response.status_code == 200
        assert response.json()["code"] == 0

        async with stack.store.engine.connect() as connection:
            outbox = (
                await connection.execute(
                    select(outbox_events).where(
                        outbox_events.c.aggregate_type == "binding"
                    )
                )
            ).mappings().all()
            audit = (
                await connection.execute(
                    select(audit_logs).where(audit_logs.c.target_type == "binding")
                )
            ).mappings().all()
        revision_after = await stack.store.read_revision(tenant_id="tenant-a")

    assert len(outbox) == 1
    assert outbox[0]["event_type"] == "config.changed"
    assert outbox[0]["aggregate_id"].startswith("bind_")
    assert outbox[0]["status"] == "pending"
    assert outbox[0]["revision"] == revision_after

    assert len(audit) == 1
    assert audit[0]["action"] == "binding.create"
    assert audit[0]["actor_id"] == "admin-a"
    assert audit[0]["request_id"] == "req-S-A12-binding"

    assert revision_after == revision_before + 1
