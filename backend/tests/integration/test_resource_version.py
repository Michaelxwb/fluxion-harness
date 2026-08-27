from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.console_helpers import (
    console_stack,
    create_resource,
    publish_resource,
    runtime_profile_spec,
    tenant_headers,
)

from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import audit_logs
from fluxion.resources import ResourceKind


@pytest.mark.asyncio
async def test_E_C101_published_resource_cannot_be_updated_in_place() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-E-C101-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-E-C101-publish",
        )
        response = await stack.client.put(
            "/api/v1/resources/runtime_profile/assistant/versions/1",
            json={"spec": runtime_profile_spec()},
            headers=tenant_headers(request_id="req-E-C101-update"),
        )

        payload = response.json()
        assert response.status_code == 409
        assert isinstance(payload["code"], int)
        assert payload["code"] != 0
        assert "已发布版本不可直接修改" in payload["message"]


@pytest.mark.asyncio
async def test_B_C101_concurrent_publish_on_same_base_allows_only_one_success() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-B-C101-create",
        )

        async def _publish(request_id: str) -> int:
            response = await publish_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                expected_base_version="1",
                request_id=request_id,
            )
            return response.status_code

        statuses = await asyncio.gather(
            _publish("req-B-C101-a"),
            _publish("req-B-C101-b"),
        )

        assert sorted(statuses) == [200, 409]


@pytest.mark.asyncio
async def test_publish_writes_audit_record(tmp_path: Path) -> None:
    async with console_stack(db_path=tmp_path / "audit.db") as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-audit-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-audit-publish",
        )
        reader = SQLiteRegistryStore(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
        await reader.initialize()
        try:
            async with reader.engine.connect() as connection:
                rows = (
                    await connection.execute(
                        select(audit_logs).order_by(audit_logs.c.created_at)
                    )
                ).mappings().all()
        finally:
            await reader.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "publish"
    assert row["tenant_id"] == "tenant-a"
    assert row["actor_id"] == "admin-a"
    assert row["request_id"] == "req-audit-publish"
    assert row["target_type"] == "runtime_profile"
    assert row["target_id"] == "assistant"
    assert row["publish_id"] == row["after_json"]["publish_id"]
    assert row["after_json"]["version"] == "1"
    assert row["after_json"]["status"] == "published"
    assert row["after_json"]["revision"] == 1
    assert row["after_json"]["approval_id"] is None
