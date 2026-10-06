from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.delivery.artifact_client import ArtifactResolveClient
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.service import (
    DELIVERY_RESERVATION_LEASE_SEC,
    MAX_TENANTS_PER_TICK,
    DeliveryLoop,
)
from muad_agent_worker.infrastructure.models.task import (
    DeliveryRoute,
    TaskEvent,
    TaskExecution,
)
from muad_agent_worker.metrics import value
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import default_platform_settings, parse_platform_settings

from agent_worker.conftest import TenantContext
from agent_worker.helpers import (
    DELIVERED_ENVELOPE,
    fetch_events,
    fetch_task,
    persist_task,
    sample_route,
)


class _TaskSettingsClient:
    """注入的桩快照源：按 schema 构造一份只覆盖 `task` 分组的平台设置文档。"""

    def __init__(self, **task_overrides: int) -> None:
        self._snapshot = PlatformSettingsSnapshot(
            revision=1,
            settings=asdict(parse_platform_settings({"task": task_overrides})),
        )

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return self._snapshot


def _task_settings_client(**task_overrides: int) -> _TaskSettingsClient:
    return _TaskSettingsClient(**task_overrides)




def _handler(status_code: int, calls: list[httpx.Request]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code, json=DELIVERED_ENVELOPE)

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
    settings_client = _task_settings_client(delivery_max_attempts=2)
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
            tenant.settings,
            settings_client=settings_client,
        )
        first = await loop.run_once(now=t0)
        assert first is not None
        assert first.http_status == 500
        after_first = await fetch_task(tenant, task.id)
        assert after_first.delivery_status == "PENDING", "还有预算就仍是待投递，不是终态失败"
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
        assert refreshed.delivery_attempts == default_platform_settings().task.delivery_max_attempts
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
    assert refreshed.delivery_status == "PENDING", "还有预算就仍是待投递，不是终态失败"
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
        return httpx.Response(200, json=DELIVERED_ENVELOPE)

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
    settings_client = _task_settings_client(delivery_max_attempts=2)
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
        first = DeliveryLoop(tenant.session_factory, http, tenant.settings, settings_client=settings_client)
        assert await first.run_once(now=t0) is not None
        # 模拟重启：新实例读持久化进度
        restarted = DeliveryLoop(
            tenant.session_factory, http, tenant.settings, settings_client=settings_client
        )
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
        return httpx.Response(200, json=DELIVERED_ENVELOPE)

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
    settings_client = _task_settings_client(delivery_max_attempts=1)
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
            tenant.settings,
            settings_client=settings_client,
        )
        assert await loop.run_once() is not None
    assert value("delivery_attempt_total") == attempts_before + 1
    assert value("delivery_failed_total") == failed_before + 1
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status == "FAILED"
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_FAILED"]
    assert events[0].payload_json["terminal"] is True


# --------------------------------------------------------------- 产物形态（TASK-006）


def _ref_handler(artifact_id: uuid.UUID, *, status: int = 200, calls: list[Any] | None = None) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"code": "COMMON_NOT_FOUND", "data": None})
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "storage_key": f"outbound/run-1/{artifact_id}/v1",
                    "kind": "DOCUMENT",
                    "media_type": "text/markdown",
                    "size": 12,
                    "filename": "汇总.md",
                    "checksum": "sha256:" + "0" * 64,
                    "artifact_id": str(artifact_id),
                },
            },
        )

    return handle


async def _resolve_loop(tenant: TenantContext, handler: Any, *, deliver_calls: list[Any]) -> DeliveryLoop:
    return DeliveryLoop(
        tenant.session_factory,
        HttpDeliveryClient(
            "http://im-gateway",
            httpx.AsyncClient(transport=httpx.MockTransport(_handler(200, deliver_calls))),
        ),
        tenant.settings,
        ArtifactResolveClient("http://agent-runtime", transport=httpx.MockTransport(handler)),
    )


async def test_completed_task_sends_the_artifact_instead_of_a_bare_id(tenant: TenantContext) -> None:
    """有产物就**发产物**：用户收到文件，而不是一串 UUID（FEAT-09 / S-07 的硬要求）。"""
    artifact_id = uuid.uuid4()
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
        result_artifact_id=artifact_id,
    )
    deliver_calls: list[Any] = []
    loop = await _resolve_loop(tenant, _ref_handler(artifact_id), deliver_calls=deliver_calls)

    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is True
    assert len(deliver_calls) == 1
    body = json.loads(deliver_calls[0].content)
    assert body["message"]["type"] == "artifact"
    assert body["message"]["artifact"]["artifact_id"] == str(artifact_id)
    assert body["message"]["text"] == "", "产物形态不带文本载荷"
    assert body["tenant_id"] == tenant.tenant_id
    assert body["task_id"] == str(task.id)


async def test_gone_artifact_falls_back_to_the_existing_text(tenant: TenantContext) -> None:
    """产物**没了**（404）⇒ 重试多少次都不会好 ⇒ 退回文本形态，不让这条投递永远卡住。"""
    await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
        result_artifact_id=uuid.uuid4(),
    )
    deliver_calls: list[Any] = []
    loop = await _resolve_loop(tenant, _ref_handler(uuid.uuid4(), status=404), deliver_calls=deliver_calls)

    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is True
    body = json.loads(deliver_calls[0].content)
    assert body["message"]["type"] == "text", "退文本，但仍要有任务结论"
    assert body["message"]["text"]


async def test_unresolvable_artifact_is_a_retryable_failure_and_sends_nothing(
    tenant: TenantContext,
) -> None:
    """解析**没拿到结论**（5xx）⇒ 可重试，且**什么都不发**。

    不能降级成文本：那会把"该发文件却发了串 ID"**固化**下来，而那正是本需求要消灭的东西。
    """
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
        result_artifact_id=uuid.uuid4(),
    )
    deliver_calls: list[Any] = []
    loop = await _resolve_loop(tenant, _ref_handler(uuid.uuid4(), status=500), deliver_calls=deliver_calls)

    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    assert deliver_calls == [], "没拿到引用就什么都不该发"
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status != "SENT", "不能标记成已送达"


# ---------------------------------------------------- 评审回归（2026-10-06）


class _PartlyFailingSettingsClient:
    """只对某一个租户的取快照失败，其余照常——模拟「一个租户的设置源坏了」。"""

    def __init__(self, broken: str) -> None:
        self._broken = broken
        self._healthy = _task_settings_client()
        self.calls: list[str] = []

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        self.calls.append(tenant_id)
        if tenant_id == self._broken:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return await self._healthy.fetch_snapshot(tenant_id=tenant_id, trace_id=trace_id)


def _loop(tenant: TenantContext, **kwargs: Any) -> DeliveryLoop:
    """一个不真的发出去的 DeliveryLoop（本轮用例只看扫描与状态机）。"""

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DELIVERED_ENVELOPE)

    return DeliveryLoop(
        tenant.session_factory,
        HttpDeliveryClient(
            "http://im-gateway", httpx.AsyncClient(transport=httpx.MockTransport(handle))
        ),
        tenant.settings,
        **kwargs,
    )


async def _purge_tenants(tenant: TenantContext, tenant_ids: list[str]) -> None:
    """清掉本用例自造的多租户行（跨租户投递用例需要多个 tenant_id）。"""
    async with tenant.session_factory() as session, session.begin():
        for tenant_id in tenant_ids:
            for model in (TaskEvent, TaskExecution, DeliveryRoute):
                await session.execute(sa.delete(model).where(model.tenant_id == tenant_id))


async def test_malformed_200_is_not_a_delivery(tenant: TenantContext) -> None:
    """HTTP 200 但不是合法封套 ≠ 送达（评审 #11）。

    此前非法 JSON 一律返回 `delivered=True`：Worker 把行置成 SENT，却没有任何可信回执，
    任务结果再也不会重投——用户就是收不到，而系统认为已经发过了。
    """

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway ok</html>")

    task = await persist_task(
        tenant, status="COMPLETED", finished_at=datetime.now(UTC), delivery_route=sample_route()
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_status != "SENT", "没有可信回执就不能算送达"
    assert refreshed.delivered_at is None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_RETRY"]


async def test_envelope_without_the_delivered_flag_is_not_a_delivery(
    tenant: TenantContext,
) -> None:
    """缺 `delivered` 字段的 200 同样不算送达（评审 #11）——旧实现按"已送达"处理。"""

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": {"accepted": True}})

    task = await persist_task(
        tenant, status="COMPLETED", finished_at=datetime.now(UTC), delivery_route=sample_route()
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    assert (await fetch_task(tenant, task.id)).delivery_status == "PENDING"


async def test_exhausted_deliveries_leave_the_cross_tenant_scan(tenant: TenantContext) -> None:
    """预算耗尽的记录不再占用跨租户扫描名额（评审 #9）。

    此前「可重试失败」也写成 `delivery_status=FAILED`，而 FAILED 仍在待投递集合里：耗尽次数
    的记录于是永远被选中，又按最早 `create_time` 排在最前，把后面的租户整批挡死。
    """
    await persist_task(
        tenant,
        status="FAILED",
        error_code="BOOM",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
        delivery_status="FAILED",
        delivery_attempts=5,
    )

    assert await _loop(tenant)._pending_tenants() == []


async def test_tenant_cursor_reaches_tenants_behind_a_backlogged_prefix(
    tenant: TenantContext,
) -> None:
    """跨租户游标必须前进：前面 8 个租户都选不出候选时，第 9 个仍要被看到（评审 #9）。

    固定取「最早的前 8 个」时，只要那 8 个一直选不出候选（例如记录都在退避窗口里），
    后面的租户永远轮不到。
    """
    base = datetime.now(UTC) - timedelta(minutes=10)
    backlog = [f"{tenant.tenant_id}-backlog{index}" for index in range(MAX_TENANTS_PER_TICK)]
    loop = _loop(tenant)
    try:
        for index, tenant_id in enumerate(backlog):
            await persist_task(
                tenant,
                tenant_id=tenant_id,
                status="COMPLETED",
                finished_at=base,
                create_time=base + timedelta(seconds=index),
                delivery_route=sample_route(),
                delivery_attempts=1,  # 首次尝试已用掉，本轮落在退避窗口里
                update_time=datetime.now(UTC),
            )
        await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=base,
            create_time=base + timedelta(seconds=99),
            delivery_route=sample_route(),
        )

        assert await loop.run_once() is None, "前 8 个租户的记录都在退避窗口里，选不出候选"
        assert loop._tenant_cursor is not None, "看过一格就该记下来，否则下一轮还从头开始"

        # 第二轮从游标之后继续：健康租户这一格终于被看到
        assert await loop.run_once() is not None, "游标没前进，后面的租户永远轮不到"
    finally:
        await _purge_tenants(tenant, backlog)


async def test_one_tenants_settings_failure_does_not_block_the_queue(
    tenant: TenantContext,
) -> None:
    """单个租户读不到设置只影响它自己（评审 #10）。

    此前异常直接冒到 `run_forever` 的外层：较老的租户读设置失败，后面所有健康租户这一轮
    以及下一轮（同样的顺序）全部停摆。
    """
    broken = f"{tenant.tenant_id}-broken"
    base = datetime.now(UTC) - timedelta(minutes=10)
    client = _PartlyFailingSettingsClient(broken)
    loop = _loop(tenant, settings_client=client)
    try:
        await persist_task(
            tenant,
            tenant_id=broken,
            status="COMPLETED",
            finished_at=base,
            create_time=base,
            delivery_route=sample_route(),
        )
        healthy = await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=base,
            create_time=base + timedelta(seconds=1),
            delivery_route=sample_route(),
        )

        outcome = await loop.run_once()

        assert outcome is not None and outcome.sent is True
        assert outcome.task_id == healthy.id, "健康租户的投递被坏租户挡住了"
        assert client.calls[0] == broken, "坏租户排在前面"
        assert healthy.tenant_id in client.calls
    finally:
        await _purge_tenants(tenant, [broken])


async def test_abandoned_reservation_is_settled_instead_of_staying_pending(
    tenant: TenantContext,
) -> None:
    """预留后崩溃：记录不能永远停在 PENDING（评审 #12）。

    预留阶段在实际发送**之前**自增 `delivery_attempts` 并提交（崩溃不丢退避进度，这是对的），
    但状态留在 PENDING：若这次正好用掉最后一次预算，候选条件 `attempts < max` 永远为假——
    既不会重投，也没有任何路径把它结算成 FAILED。超过预留租约仍未回执的，判定为"那次尝试
    随进程没了"，直接终态失败。
    """
    max_attempts = default_platform_settings().task.delivery_max_attempts
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=DELIVERY_RESERVATION_LEASE_SEC + 1)
    abandoned = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=now,
        delivery_route=sample_route(),
        delivery_attempts=max_attempts,
        update_time=stale,
    )
    sending = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=now,
        delivery_route=sample_route(),
        delivery_attempts=max_attempts,
        update_time=now - timedelta(seconds=5),
    )

    assert await _loop(tenant).run_once(now=now) is None

    refreshed = await fetch_task(tenant, abandoned.id)
    assert refreshed.delivery_status == "FAILED", "滞留的预留必须被结算"
    events = await fetch_events(tenant, abandoned.id)
    assert [event.event_type for event in events] == ["DELIVERY_FAILED"]
    assert events[0].payload_json["reason"] == "reservation_abandoned"

    assert (
        await fetch_task(tenant, sending.id)
    ).delivery_status == "PENDING", "可能正在发送的记录不得被误判成滞留"
