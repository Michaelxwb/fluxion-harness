from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from conftest import TenantContext
from helpers import fetch_events, fetch_task, persist_task, sample_route
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.service import DeliveryLoop
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.metrics import value
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


async def test_b120_success_counts_as_attempt_with_stable_key(tenant: TenantContext) -> None:
    """成功投递也计入尝试次数，delivery_key 恒定。"""
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
    assert outcome is not None and outcome.sent is True
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status == "SENT"
    assert refreshed.delivery_attempts == 1, "成功也必须计入尝试"
    assert refreshed.delivery_key == f"task:{task.id}:final"
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_SENT"]


async def test_b120_concurrent_loops_deliver_once(tenant: TenantContext) -> None:
    """双 DeliveryLoop 并发：发送前先持久预留，只有一个真实发送。"""
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )
    calls: list[httpx.Request] = []

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"code": "0", "data": {"accepted": True}})

    transport = httpx.MockTransport(slow_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        http = HttpDeliveryClient("http://im-gateway", client)
        first = DeliveryLoop(tenant.session_factory, http, tenant.settings)
        second = DeliveryLoop(tenant.session_factory, http, tenant.settings)
        outcomes = await asyncio.gather(first.run_once(), second.run_once())
    assert len(calls) == 1, "并发投递只允许一次真实发送"
    assert sum(1 for outcome in outcomes if outcome is not None and outcome.sent) == 1
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status == "SENT"
    assert refreshed.delivery_attempts == 1
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_SENT"]


async def test_b120_restart_preserves_backoff_and_attempt_limit(tenant: TenantContext) -> None:
    """进程重启后仍保留退避进度与尝试上限，不会提前重试或超限。"""
    settings = SharedSettings(delivery_max_attempts=2)
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(500, calls))
    t0 = datetime.now(UTC)
    async with httpx.AsyncClient(transport=transport) as client:
        http = HttpDeliveryClient("http://im-gateway", client)
        first = DeliveryLoop(tenant.session_factory, http, settings)
        assert await first.run_once(now=t0) is not None
        # 模拟重启：新实例读持久化进度
        restarted = DeliveryLoop(tenant.session_factory, http, settings)
        assert await restarted.run_once(now=t0 + timedelta(seconds=1)) is None, "退避期内不得重试"
        assert await restarted.run_once(now=t0 + timedelta(seconds=11)) is not None
        assert await restarted.run_once(now=t0 + timedelta(seconds=100)) is None, "达到上限不得再扫"
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status == "FAILED"
    assert refreshed.delivery_attempts == 2
    assert len(calls) == 2


async def test_b120_terminal_result_committed_before_gateway_call(tenant: TenantContext) -> None:
    """先提交终态结果与本次尝试预留，再调用 Gateway。"""
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        result_json={"ok": True},
        delivery_route=sample_route(),
    )
    observed: list[tuple[str, int, str, Any]] = []

    async def inspecting_handler(request: httpx.Request) -> httpx.Response:
        async with tenant.session_factory() as session:
            row = await session.get(TaskExecution, task.id)
        assert row is not None
        observed.append((row.status, row.delivery_attempts, row.delivery_status, row.result_json))
        return httpx.Response(200, json={"code": "0", "data": {"accepted": True}})

    transport = httpx.MockTransport(inspecting_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        assert await loop.run_once() is not None
    assert observed == [("COMPLETED", 1, "PENDING", {"ok": True})]


async def test_b120_all_terminal_statuses_are_deliverable(tenant: TenantContext) -> None:
    """COMPLETED / FAILED / CANCELLED 终态都按 delivery_mode 投递。"""
    tasks = [
        await persist_task(
            tenant,
            status=status,
            error_code="BOOM" if status == "FAILED" else None,
            finished_at=datetime.now(UTC),
            delivery_route=sample_route(),
        )
        for status in ("COMPLETED", "FAILED", "CANCELLED")
    ]
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(200, calls))
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        for _ in tasks:
            assert await loop.run_once() is not None
    assert len(calls) == 3
    for task in tasks:
        assert (await fetch_task(tenant, task.id)).delivery_status == "SENT"


async def test_b120_terminal_failure_records_metric_and_audit(tenant: TenantContext) -> None:
    """投递失败留下审计事件并累加 delivery_attempt_total / delivery_failed_total。"""
    attempts_before = value("delivery_attempt_total")
    failed_before = value("delivery_failed_total")
    settings = SharedSettings(delivery_max_attempts=1)
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(500, calls))
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            settings,
        )
        assert await loop.run_once() is not None
    assert value("delivery_attempt_total") == attempts_before + 1
    assert value("delivery_failed_total") == failed_before + 1
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status == "FAILED"
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_FAILED"]
    assert events[0].payload_json["terminal"] is True
