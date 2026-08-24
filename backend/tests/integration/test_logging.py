from __future__ import annotations

import json
import logging

import pytest
from tests.console_helpers import console_stack, create_resource, publish_resource, tenant_headers

from fluxion.resources import ResourceKind


def _access_events(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for record in records:
        if record.name != "fluxion.console.access":
            continue
        events.append(json.loads(record.getMessage()))
    return events


@pytest.mark.asyncio
async def test_S_C112_request_log_contains_required_context_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="fluxion.console.access")
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C112-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C112-publish",
        )
        await stack.client.get(
            "/api/v1/resources/runtime_profile/assistant",
            headers=tenant_headers(
                tenant_id="tenant-a",
                actor_id="admin-a",
                request_id="req-S-C112-detail",
                trace_id="trace-S-C112",
            ),
        )

    detail_events = [
        event for event in _access_events(caplog.records)
        if event.get("request_id") == "req-S-C112-detail"
    ]
    assert len(detail_events) == 1
    event = detail_events[0]
    for field in (
        "timestamp",
        "level",
        "service",
        "environment",
        "event",
        "request_id",
        "trace_id",
        "tenant_id",
        "actor_id",
        "method",
        "route",
        "status_code",
        "biz_code",
        "latency_ms",
    ):
        assert field in event
    assert event["trace_id"] == "trace-S-C112"
    assert event["tenant_id"] == "tenant-a"
    assert event["actor_id"] == "admin-a"
    assert event["status_code"] == 200
    assert event["biz_code"] == 0


@pytest.mark.asyncio
async def test_E_C111_logs_redact_sensitive_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="fluxion.console.access")
    async with console_stack() as stack:
        await stack.client.get(
            "/api/v1/resources/runtime_profile/missing?token=raw-token&bind_code=ABC123",
            headers={
                **tenant_headers(request_id="req-E-C111-redact"),
                "Authorization": "Bearer raw-token",
                "X-Bind-Code": "ABC123",
                "X-Api-Key": "raw-api-key",
            },
        )

    log_text = caplog.text
    assert "raw-token" not in log_text
    assert "ABC123" not in log_text
    assert "raw-api-key" not in log_text
    redact_events = [
        event for event in _access_events(caplog.records)
        if event.get("request_id") == "req-E-C111-redact"
    ]
    assert len(redact_events) == 1
    assert "[REDACTED]" in json.dumps(redact_events[0], ensure_ascii=False)
