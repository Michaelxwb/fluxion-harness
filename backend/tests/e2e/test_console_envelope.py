from __future__ import annotations

import json
import logging

import pytest
from tests.console_helpers import (
    console_stack,
    create_resource,
    publish_resource,
    tenant_headers,
)

from fluxion.errors.console import RESOURCE_NOT_FOUND
from fluxion.resources import ResourceKind


@pytest.mark.asyncio
async def test_unknown_route_returns_unified_404_envelope() -> None:
    async with console_stack() as stack:
        response = await stack.client.get(
            "/api/v1/no/such/route",
            headers=tenant_headers(request_id="req-404"),
        )

    payload = response.json()
    assert response.status_code == 404
    assert payload["code"] == RESOURCE_NOT_FOUND
    assert payload["message"] == "not found"
    assert payload["data"] is None
    assert payload["request_id"] == "req-404"


@pytest.mark.asyncio
async def test_method_not_allowed_returns_unified_envelope() -> None:
    async with console_stack() as stack:
        response = await stack.client.post(
            "/api/v1/resources/runtime_profile/assistant",
            json={},
            headers=tenant_headers(request_id="req-405"),
        )

    payload = response.json()
    assert response.status_code == 405
    assert payload["code"] != 0
    assert payload["data"] is None


@pytest.mark.asyncio
async def test_logs_redact_mid_word_sensitive_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="fluxion.console.access")
    async with console_stack() as stack:
        await stack.client.get(
            "/api/v1/resources/runtime_profile/missing"
            "?x_api_key_value=mid-key-secret&client_secret_store=mid-word-secret",
            headers={
                **tenant_headers(request_id="req-midword-redact"),
                "X-Client-Secret-Store": "mid-word-secret",
            },
        )

    assert "mid-key-secret" not in caplog.text
    assert "mid-word-secret" not in caplog.text
    access_events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "fluxion.console.access"
        and json.loads(record.getMessage()).get("request_id") == "req-midword-redact"
    ]
    assert len(access_events) == 1
    assert "[REDACTED]" in json.dumps(access_events[0], ensure_ascii=False)


@pytest.mark.asyncio
async def test_publish_expected_base_version_is_optimistic_lock() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
            request_id="req-v1-create",
        )
        v1 = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
            request_id="req-v1-publish",
        )
        assert v1.status_code == 200

        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="2",
            request_id="req-v2-create",
        )
        v2 = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="2",
            request_id="req-v2-publish",
        )
        assert v2.status_code == 200  # base 仍为 v1，expected "1" 命中

        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="3",
            request_id="req-v3-create",
        )
        stale = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="3",
            expected_base_version="1",
            request_id="req-v3-publish",
        )
        assert stale.status_code == 409  # base 已推进到 v2，v1 是 stale base

        # 不带 expected_base_version 时不做乐观锁
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="4",
            request_id="req-v4-create",
        )
        unlocked = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="4",
            expected_base_version=None,
            request_id="req-v4-publish",
        )
        assert unlocked.status_code == 200


@pytest.mark.asyncio
async def test_publish_bumps_tenant_config_revision() -> None:
    async with console_stack() as stack:
        assert await stack.store.read_revision(tenant_id="tenant-a") == 0
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-rev-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-rev-publish",
        )
        assert await stack.store.read_revision(tenant_id="tenant-a") == 1

