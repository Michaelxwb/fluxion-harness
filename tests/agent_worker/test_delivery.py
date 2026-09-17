from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from conftest import TenantContext
from helpers import fetch_events, fetch_task, persist_task, sample_route
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.service import DeliveryLoop
from muad_common import SharedSettings


def _handler(status_code: int, calls: list[httpx.Request]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code, json={"code": "0", "data": {"accepted": True}})

    return handle


async def test_delivery_success_marks_sent(tenant: TenantContext) -> None:
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(200, calls))
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()
    assert outcome is not None
    assert outcome.sent is True
    assert outcome.http_status == 200
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["task_id"] == str(task.id)
    assert body["delivery_key"] == f"task:{task.id}:final"
    assert body["message"]["text"]
    assert body["artifact_ids"] == []
    assert body["route"]["channel"] == "WECOM"
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status == "SENT"
    assert refreshed.delivered_at is not None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_SENT"]


async def test_delivery_500_retries_with_backoff_then_gives_up(tenant: TenantContext) -> None:
    settings = SharedSettings(delivery_max_attempts=2)
    task = await persist_task(
        tenant,
        status="FAILED",
        error_code="BOOM",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(500, calls))
    t0 = datetime.now(UTC)
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            settings,
        )
        first = await loop.run_once(now=t0)
        assert first is not None
        assert first.http_status == 500
        after_first = await fetch_task(tenant, task.id)
        assert after_first.delivery_status == "FAILED"
        assert after_first.delivery_attempts == 1

        assert await loop.run_once(now=t0 + timedelta(seconds=1)) is None
        assert len(calls) == 1

        second = await loop.run_once(now=t0 + timedelta(seconds=11))
        assert second is not None
        after_second = await fetch_task(tenant, task.id)
        assert after_second.delivery_attempts == 2
        assert after_second.delivery_status == "FAILED"
        assert await loop.run_once(now=t0 + timedelta(seconds=100)) is None
    assert len(calls) == 2
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_RETRY", "DELIVERY_FAILED"]


async def test_delivery_400_fails_immediately(tenant: TenantContext) -> None:
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(400, calls))
    t0 = datetime.now(UTC)
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once(now=t0)
        assert outcome is not None
        assert outcome.http_status == 400
        refreshed = await fetch_task(tenant, task.id)
        assert refreshed.delivery_status == "FAILED"
        assert refreshed.delivery_attempts == tenant.settings.delivery_max_attempts
        assert await loop.run_once(now=t0 + timedelta(seconds=100)) is None
    assert len(calls) == 1
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_FAILED"]
    assert events[0].payload_json["terminal"] is True


async def test_delivery_transport_error_retries(tenant: TenantContext) -> None:
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()
    assert outcome is not None
    assert outcome.http_status is None
    assert outcome.error is not None
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status == "FAILED"
    assert refreshed.delivery_attempts == 1
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_RETRY"]


async def test_delivery_skips_non_terminal_and_none_mode(tenant: TenantContext) -> None:
    await persist_task(tenant, status="RUNNING", delivery_route=sample_route())
    await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_mode="NONE",
        delivery_status="NONE",
    )
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(200, calls))
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        assert await loop.run_once() is None
    assert calls == []
