"""独立投递：`task_delivery`/`task_delivery_attempt` 是唯一状态源（design §3.2.11 / §3.5.9）。

不得 mock 的真实边界：真实 PostgreSQL（唯一键、`FOR UPDATE SKIP LOCKED`、租约 CAS）；
网关/收件箱是真实 HTTP 客户端 + `httpx.MockTransport` 双重，状态全部落真库。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.application.task_events import EntityType
from muad_agent_worker.delivery.artifact_client import ArtifactResolveClient
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.records import (
    DeliveryKind,
    DeliveryState,
    execution_delivery_key,
)
from muad_agent_worker.delivery.service import (
    DELIVERY_RESERVATION_LEASE_SEC,
    MAX_TENANTS_PER_TICK,
    DeliveryLoop,
)
from muad_agent_worker.infrastructure.models.task import (
    DeliveryRoute,
    TaskDelivery,
    TaskDeliveryAttempt,
    TaskEvent,
    TaskExecution,
)
from muad_agent_worker.metrics import value
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import DeliveryRouteInput
from muad_contracts.platform_settings import default_platform_settings, parse_platform_settings
from sqlalchemy import select

from agent_worker.conftest import TenantContext
from agent_worker.helpers import DELIVERED_ENVELOPE, fetch_events, fetch_task, persist_task, sample_route


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


# --------------------------------------------------------------------- 种数据


async def _route_id(
    tenant: TenantContext,
    route: DeliveryRouteInput | None = None,
    *,
    status: str = "ACTIVE",
    tenant_id: str | None = None,
) -> uuid.UUID:
    async with tenant.session_factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant_id or tenant.tenant_id,
            platform_user_id=uuid.uuid4(),
            route=route or sample_route(),
        )
        await session.execute(
            sa.update(DeliveryRoute)
            .where(DeliveryRoute.id == route_id)
            .values(status=status)
        )
    return route_id


async def _seed_delivery(
    tenant: TenantContext,
    *,
    execution: TaskExecution | None = None,
    kind: DeliveryKind = DeliveryKind.PUSH,
    status: str = str(DeliveryState.PENDING),
    attempt_count: int = 0,
    update_time: datetime | None = None,
    lease_owner: str | None = None,
    lease_until: datetime | None = None,
    route_status: str = "ACTIVE",
    delivery_key: str | None = None,
) -> tuple[TaskExecution, TaskDelivery]:
    task = execution or await persist_task(
        tenant, status="COMPLETED", finished_at=datetime.now(UTC)
    )
    route_id = await _route_id(tenant, status=route_status, tenant_id=task.tenant_id)
    delivery = TaskDelivery(
        id=uuid.uuid4(),
        tenant_id=task.tenant_id,
        execution_id=task.id,
        delivery_route_id=route_id,
        delivery_kind=str(kind),
        delivery_key=delivery_key or execution_delivery_key(task.id),
        status=status,
        attempt_count=attempt_count,
        lease_owner=lease_owner,
        lease_until=lease_until,
        update_time=update_time or datetime.now(UTC),
    )
    async with tenant.session_factory() as session, session.begin():
        session.add(delivery)
    return task, delivery


async def _fetch_delivery(tenant: TenantContext, delivery_id: uuid.UUID) -> TaskDelivery:
    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskDelivery, delivery_id)
    if row is None:
        raise AssertionError(f"delivery {delivery_id} not found")
    return row


async def _fetch_attempts(tenant: TenantContext, delivery_id: uuid.UUID) -> list[TaskDeliveryAttempt]:
    async with tenant.session_factory() as session, session.begin():
        rows = await session.scalars(
            select(TaskDeliveryAttempt)
            .where(TaskDeliveryAttempt.delivery_id == delivery_id)
            .order_by(TaskDeliveryAttempt.attempt_no.asc())
        )
    return list(rows)


async def _purge_tenants(tenant: TenantContext, tenant_ids: list[str]) -> None:
    """清掉本用例自造的多租户行（跨租户投递用例需要多个 tenant_id）。"""
    async with tenant.session_factory() as session, session.begin():
        for tenant_id in tenant_ids:
            await session.execute(
                sa.delete(TaskDeliveryAttempt).where(
                    TaskDeliveryAttempt.delivery_id.in_(
                        select(TaskDelivery.id).where(TaskDelivery.tenant_id == tenant_id)
                    )
                )
            )
            await session.execute(
                sa.delete(TaskDelivery).where(TaskDelivery.tenant_id == tenant_id)
            )
            await session.execute(
                sa.delete(TaskEvent).where(TaskEvent.tenant_id == tenant_id)
            )
            await session.execute(
                sa.delete(TaskExecution).where(TaskExecution.tenant_id == tenant_id)
            )
            await session.execute(
                sa.delete(DeliveryRoute).where(DeliveryRoute.tenant_id == tenant_id)
            )


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


# --------------------------------------------------------------------- 成功与状态机


async def test_delivery_success_marks_sent(tenant: TenantContext) -> None:
    task, delivery = await _seed_delivery(tenant)
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(200, calls))) as client:
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
    assert body["delivery_key"] == f"execution:{task.id}:final"
    assert body["message"]["text"]
    assert body["artifact_ids"] == []
    assert body["route"]["channel"] == "WECOM"
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "SENT"
    assert refreshed.sent_at is not None
    assert refreshed.attempt_count == 1
    attempts = await _fetch_attempts(tenant, delivery.id)
    assert [(attempt.attempt_no, attempt.status) for attempt in attempts] == [(1, "SENT")]
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert [event.event_type for event in events] == ["SENT"]
    # 投递不改业务终态
    assert (await fetch_task(tenant, task.id)).status == "COMPLETED"


async def test_delivery_500_retries_with_backoff_then_gives_up(tenant: TenantContext) -> None:
    settings_client = _task_settings_client(delivery_max_attempts=2)
    _, delivery = await _seed_delivery(tenant)
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
        after_first = await _fetch_delivery(tenant, delivery.id)
        assert after_first.status == "PENDING", "还有预算就仍是待投递，不是终态失败"
        assert after_first.attempt_count == 1
        assert after_first.last_error_code == "HTTP_500"

        assert await loop.run_once(now=t0 + timedelta(seconds=1)) is None
        assert len(calls) == 1

        second = await loop.run_once(now=t0 + timedelta(seconds=11))
        assert second is not None
        after_second = await _fetch_delivery(tenant, delivery.id)
        assert after_second.attempt_count == 2
        assert after_second.status == "FAILED"
        assert await loop.run_once(now=t0 + timedelta(seconds=100)) is None
    assert len(calls) == 2
    attempts = await _fetch_attempts(tenant, delivery.id)
    assert [(attempt.attempt_no, attempt.status) for attempt in attempts] == [
        (1, "FAILED"),
        (2, "FAILED"),
    ]
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert [event.event_type for event in events] == ["FAILED"]
    assert events[0].payload_json["terminal"] is True


async def test_delivery_400_fails_immediately(tenant: TenantContext) -> None:
    _, delivery = await _seed_delivery(tenant)
    calls: list[httpx.Request] = []
    t0 = datetime.now(UTC)

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"code": "COMMON_INVALID_PARAM", "data": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once(now=t0)
        assert outcome is not None
        assert outcome.http_status == 400
        refreshed = await _fetch_delivery(tenant, delivery.id)
        assert refreshed.status == "FAILED"
        assert refreshed.attempt_count == 1, "4xx 是确定性失败，不消耗更多预算"
        assert refreshed.last_error_code == "COMMON_INVALID_PARAM"
        assert await loop.run_once(now=t0 + timedelta(seconds=100)) is None
    assert len(calls) == 1
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert [event.event_type for event in events] == ["FAILED"]


async def test_delivery_transport_error_retries(tenant: TenantContext) -> None:
    _, delivery = await _seed_delivery(tenant)

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()
    assert outcome is not None
    assert outcome.http_status is None
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "PENDING", "还有预算就仍是待投递，不是终态失败"
    assert refreshed.attempt_count == 1
    assert refreshed.last_error_code == "DELIVERY_TRANSPORT_ERROR"
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert events == [], "可重试失败不是终态事件，原因落在 attempt 上"


async def test_delivery_timeout_is_a_retryable_failure(tenant: TenantContext) -> None:
    """网关超时 = 没拿到结论：可重试，不写 SENT，也不烧掉全部预算。"""
    _, delivery = await _seed_delivery(tenant)

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("gateway timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "PENDING"
    assert refreshed.attempt_count == 1
    assert refreshed.last_error_code == "DELIVERY_TRANSPORT_ERROR"


async def test_delivery_skips_non_terminal_execution(tenant: TenantContext) -> None:
    await _seed_delivery(
        tenant, execution=await persist_task(tenant, status="RUNNING")
    )
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(200, calls))) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        assert await loop.run_once() is None
    assert calls == []


async def test_success_counts_attempt_with_stable_key(tenant: TenantContext) -> None:
    task, delivery = await _seed_delivery(tenant)
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(200, calls))) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()
    assert outcome is not None and outcome.sent is True
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.attempt_count == 1, "成功也必须计入尝试"
    assert refreshed.delivery_key == f"execution:{task.id}:final"


async def test_concurrent_loops_deliver_once(tenant: TenantContext) -> None:
    """双 DeliveryLoop 并发：发送前先持久预留，只有一个真实发送。"""
    _, delivery = await _seed_delivery(tenant)
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
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "SENT"
    assert refreshed.attempt_count == 1


async def test_restart_preserves_backoff_and_attempt_limit(tenant: TenantContext) -> None:
    """进程重启后仍保留退避进度与尝试上限，不会提前重试或超限。"""
    settings_client = _task_settings_client(delivery_max_attempts=2)
    _, delivery = await _seed_delivery(tenant)
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_handler(500, calls))
    t0 = datetime.now(UTC)
    async with httpx.AsyncClient(transport=transport) as client:
        http = HttpDeliveryClient("http://im-gateway", client)
        first = DeliveryLoop(
            tenant.session_factory, http, tenant.settings, settings_client=settings_client
        )
        assert await first.run_once(now=t0) is not None
        # 模拟重启：新实例读持久化进度
        restarted = DeliveryLoop(
            tenant.session_factory, http, tenant.settings, settings_client=settings_client
        )
        assert await restarted.run_once(now=t0 + timedelta(seconds=1)) is None, "退避期内不得重试"
        assert await restarted.run_once(now=t0 + timedelta(seconds=11)) is not None
        assert await restarted.run_once(now=t0 + timedelta(seconds=100)) is None, "达到上限不得再扫"
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "FAILED"
    assert refreshed.attempt_count == 2
    assert len(calls) == 2


async def test_reservation_is_committed_before_the_gateway_call(tenant: TenantContext) -> None:
    """先提交本次尝试预留，再调用 Gateway（崩溃也不丢退避进度）。"""
    task, delivery = await _seed_delivery(
        tenant, execution=await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=datetime.now(UTC),
            result_json={"ok": True},
        )
    )
    observed: list[tuple[str, int, str | None, Any]] = []

    async def inspecting_handler(request: httpx.Request) -> httpx.Response:
        async with tenant.session_factory() as session:
            row = await session.get(TaskDelivery, delivery.id)
            execution = await session.get(TaskExecution, task.id)
            attempts = list(
                await session.scalars(
                    select(TaskDeliveryAttempt).where(
                        TaskDeliveryAttempt.delivery_id == delivery.id
                    )
                )
            )
        assert row is not None and execution is not None
        observed.append((row.status, row.attempt_count, row.lease_owner, execution.result_json))
        assert [attempt.status for attempt in attempts] == ["PENDING"]
        return httpx.Response(200, json=DELIVERED_ENVELOPE)

    transport = httpx.MockTransport(inspecting_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        assert await loop.run_once() is not None
    assert len(observed) == 1
    state, attempt_count, lease_owner, result_json = observed[0]
    assert (state, attempt_count, result_json) == ("PENDING", 1, {"ok": True})
    assert lease_owner is not None, "发送前必须已持有租约"


async def test_all_terminal_statuses_are_deliverable(tenant: TenantContext) -> None:
    deliveries = []
    for status in ("COMPLETED", "FAILED", "CANCELLED"):
        _, delivery = await _seed_delivery(
            tenant,
            execution=await persist_task(
                tenant,
                status=status,
                error_code="BOOM" if status == "FAILED" else None,
                finished_at=datetime.now(UTC),
            ),
        )
        deliveries.append(delivery)
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(200, calls))) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        for _ in deliveries:
            assert await loop.run_once() is not None
    assert len(calls) == 3
    for delivery in deliveries:
        assert (await _fetch_delivery(tenant, delivery.id)).status == "SENT"


async def test_terminal_failure_records_metric_and_audit(tenant: TenantContext) -> None:
    attempts_before = value("delivery_attempt_total")
    failed_before = value("delivery_failed_total")
    settings_client = _task_settings_client(delivery_max_attempts=1)
    _, delivery = await _seed_delivery(tenant)
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
    assert (await _fetch_delivery(tenant, delivery.id)).status == "FAILED"
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert [event.event_type for event in events] == ["FAILED"]


# --------------------------------------------------------------------- 租约栅栏


async def test_taken_over_lease_cannot_confirm(tenant: TenantContext) -> None:
    """旧租约不得确认：发送期间被别的副本接手，本次尝试的回执不能写 SENT。"""
    _, delivery = await _seed_delivery(tenant)

    async def stealing_handler(request: httpx.Request) -> httpx.Response:
        async with tenant.session_factory() as session, session.begin():
            await session.execute(
                sa.update(TaskDelivery)
                .where(TaskDelivery.id == delivery.id)
                .values(
                    lease_owner="another-worker",
                    lease_until=datetime.now(UTC) + timedelta(seconds=DELIVERY_RESERVATION_LEASE_SEC),
                    attempt_count=TaskDelivery.attempt_count + 1,
                )
            )
        return httpx.Response(200, json=DELIVERED_ENVELOPE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(stealing_handler)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()
    assert outcome is not None and outcome.sent is False
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "PENDING"
    assert refreshed.lease_owner == "another-worker"
    assert refreshed.sent_at is None
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert events == [], "旧租约不能留下送达事件"


async def test_expired_lease_can_be_reclaimed(tenant: TenantContext) -> None:
    """崩溃后租约到期：另一个副本可以接手，尝试次数继续累加。"""
    now = datetime.now(UTC)
    _, delivery = await _seed_delivery(
        tenant,
        attempt_count=1,
        update_time=now - timedelta(seconds=60),
        lease_owner="crashed-worker",
        lease_until=now - timedelta(seconds=1),
    )
    calls: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(200, calls))) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        assert await loop.run_once(now=now) is not None
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "SENT"
    assert refreshed.attempt_count == 2
    attempts = await _fetch_attempts(tenant, delivery.id)
    assert [(attempt.attempt_no, attempt.status) for attempt in attempts] == [(2, "SENT")]


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


def _resolve_loop(tenant: TenantContext, handler: Any, *, deliver_calls: list[Any]) -> DeliveryLoop:
    return DeliveryLoop(
        tenant.session_factory,
        HttpDeliveryClient(
            "http://im-gateway",
            httpx.AsyncClient(transport=httpx.MockTransport(_handler(200, deliver_calls))),
        ),
        tenant.settings,
        ArtifactResolveClient("http://agent-runtime", transport=httpx.MockTransport(handler)),
    )


async def test_completed_task_sends_the_artifact_instead_of_a_bare_id(
    tenant: TenantContext,
) -> None:
    """有产物就**发产物**：用户收到文件，而不是一串 UUID（FEAT-09 / S-07 的硬要求）。"""
    artifact_id = uuid.uuid4()
    task, _ = await _seed_delivery(
        tenant,
        execution=await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=datetime.now(UTC),
            result_artifact_id=artifact_id,
        ),
    )
    deliver_calls: list[Any] = []
    loop = _resolve_loop(tenant, _ref_handler(artifact_id), deliver_calls=deliver_calls)

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
    await _seed_delivery(
        tenant,
        execution=await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=datetime.now(UTC),
            result_artifact_id=uuid.uuid4(),
        ),
    )
    deliver_calls: list[Any] = []
    loop = _resolve_loop(
        tenant, _ref_handler(uuid.uuid4(), status=404), deliver_calls=deliver_calls
    )

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
    _, delivery = await _seed_delivery(
        tenant,
        execution=await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=datetime.now(UTC),
            result_artifact_id=uuid.uuid4(),
        ),
    )
    deliver_calls: list[Any] = []
    loop = _resolve_loop(
        tenant, _ref_handler(uuid.uuid4(), status=500), deliver_calls=deliver_calls
    )

    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    assert deliver_calls == [], "没拿到引用就什么都不该发"
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "PENDING"
    assert refreshed.last_error_code == "DELIVERY_ARTIFACT_UNRESOLVED"


# ---------------------------------------------------- 评审回归（回执与队列）


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


async def test_malformed_200_is_not_a_delivery(tenant: TenantContext) -> None:
    """HTTP 200 但不是合法封套 ≠ 送达：没有可信回执就不能算发出去了。"""

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway ok</html>")

    _, delivery = await _seed_delivery(tenant)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    refreshed = await _fetch_delivery(tenant, delivery.id)
    assert refreshed.status == "PENDING"
    assert refreshed.sent_at is None
    assert refreshed.last_error_code == "DELIVERY_UNCONFIRMED"
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert events == []


async def test_envelope_without_the_delivered_flag_is_not_a_delivery(
    tenant: TenantContext,
) -> None:
    """缺 `delivered` 字段的 200 同样不算送达。"""

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": {"accepted": True}})

    _, delivery = await _seed_delivery(tenant)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", client),
            tenant.settings,
        )
        outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    assert (await _fetch_delivery(tenant, delivery.id)).status == "PENDING"


async def test_exhausted_deliveries_leave_the_cross_tenant_scan(tenant: TenantContext) -> None:
    """预算耗尽的记录不再占用跨租户扫描名额。

    此前「可重试失败」也写成 FAILED 却仍在待投递集合里：耗尽次数的记录于是永远被选中，
    又按最早 `create_time` 排在最前，把后面的租户整批挡死。
    """
    await _seed_delivery(tenant, status="FAILED", attempt_count=5)

    assert await _loop(tenant)._pending_tenants() == []


async def test_tenant_cursor_reaches_tenants_behind_a_backlogged_prefix(
    tenant: TenantContext,
) -> None:
    """跨租户游标必须前进：前面 8 个租户都选不出候选时，第 9 个仍要被看到。

    固定取「最早的前 8 个」时，只要那 8 个一直选不出候选（例如记录都在退避窗口里），
    后面的租户永远轮不到。
    """
    base = datetime.now(UTC) - timedelta(minutes=10)
    backlog = [f"{tenant.tenant_id}-backlog{index}" for index in range(MAX_TENANTS_PER_TICK)]
    loop = _loop(tenant)
    try:
        for index, tenant_id in enumerate(backlog):
            await _seed_delivery(
                tenant,
                execution=await persist_task(
                    tenant,
                    tenant_id=tenant_id,
                    status="COMPLETED",
                    finished_at=base,
                    create_time=base + timedelta(seconds=index),
                ),
                attempt_count=1,  # 首次尝试已用掉，本轮落在退避窗口里
                update_time=datetime.now(UTC),
            )
        healthy_execution = await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=base,
            create_time=base + timedelta(seconds=99),
        )
        await _seed_delivery(tenant, execution=healthy_execution)

        assert await loop.run_once() is None, "前 8 个租户的记录都在退避窗口里，选不出候选"
        assert loop._tenant_cursor is not None, "看过一格就该记下来，否则下一轮还从头开始"

        # 第二轮从游标之后继续：健康租户这一格终于被看到
        assert await loop.run_once() is not None, "游标没前进，后面的租户永远轮不到"
    finally:
        await _purge_tenants(tenant, backlog)


async def test_one_tenants_settings_failure_does_not_block_the_queue(
    tenant: TenantContext,
) -> None:
    """单个租户读不到设置只影响它自己，不连坐整条跨租户队列。"""
    broken = f"{tenant.tenant_id}-broken"
    base = datetime.now(UTC) - timedelta(minutes=10)
    client = _PartlyFailingSettingsClient(broken)
    loop = _loop(tenant, settings_client=client)
    try:
        await _seed_delivery(
            tenant,
            execution=await persist_task(
                tenant,
                tenant_id=broken,
                status="COMPLETED",
                finished_at=base,
                create_time=base,
            ),
        )
        healthy_execution = await persist_task(
            tenant,
            status="COMPLETED",
            finished_at=base,
            create_time=base + timedelta(seconds=1),
        )
        await _seed_delivery(tenant, execution=healthy_execution)

        outcome = await loop.run_once()

        assert outcome is not None and outcome.sent is True
        assert outcome.execution_id == healthy_execution.id, "健康租户的投递被坏租户挡住了"
        assert client.calls[0] == broken, "坏租户排在前面"
        assert healthy_execution.tenant_id in client.calls
    finally:
        await _purge_tenants(tenant, [broken])


async def test_abandoned_reservation_is_settled_instead_of_staying_pending(
    tenant: TenantContext,
) -> None:
    """预留后崩溃：记录不能永远停在 PENDING。

    预留在实际发送**之前**自增 `attempt_count` 并提交，若这次正好用掉最后一次预算，
    候选条件 `attempt_count < max` 永远为假——既不会重投，也没有任何路径把它结算成
    FAILED。超过预留租约仍未回执的，判定为"那次尝试随进程没了"，直接终态失败。
    """
    max_attempts = default_platform_settings().task.delivery_max_attempts
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=DELIVERY_RESERVATION_LEASE_SEC + 1)
    _, abandoned = await _seed_delivery(
        tenant,
        attempt_count=max_attempts,
        update_time=stale,
        lease_owner="crashed-worker",
        lease_until=stale,
    )
    _, sending = await _seed_delivery(
        tenant,
        attempt_count=max_attempts,
        update_time=now - timedelta(seconds=5),
        lease_owner="live-worker",
        lease_until=now + timedelta(seconds=5),
    )

    assert await _loop(tenant).run_once(now=now) is None

    refreshed = await _fetch_delivery(tenant, abandoned.id)
    assert refreshed.status == "FAILED", "滞留的预留必须被结算"
    events = await fetch_events(tenant, abandoned.id, entity_type=EntityType.DELIVERY)
    assert [event.event_type for event in events] == ["FAILED"]
    assert events[0].payload_json["reason"] == "reservation_abandoned"

    assert (
        await _fetch_delivery(tenant, sending.id)
    ).status == "PENDING", "可能正在发送的记录不得被误判成滞留"
