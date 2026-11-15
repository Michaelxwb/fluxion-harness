"""调度 v2 的独立投递：终态入队、Human 问题推送、路由失效与零投递纪律（TASK-022）。

不得 mock 的真实边界：真实 PostgreSQL（唯一键、租约 CAS、Schedule/Execution 行）与真实
DeliveryLoop；网关是真实 HTTP 客户端 + `httpx.MockTransport` 双重，状态全部落真库。
"""

from __future__ import annotations

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
from muad_agent_worker.application.terminal_tasks import TerminalChange, write_terminal
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.records import DeliveryKind, DeliveryState, human_delivery_key
from muad_agent_worker.delivery.service import DeliveryLoop
from muad_agent_worker.infrastructure.models.task import (
    DeliveryRoute,
    TaskDelivery,
    TaskDeliveryAttempt,
    TaskEvent,
    TaskExecution,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleRevision,
)
from muad_contracts import HumanInteractionStatus, TaskStatus, TerminalStatus
from muad_contracts.platform_settings import parse_platform_settings
from sqlalchemy import select

from agent_worker.conftest import TenantContext
from agent_worker.helpers import DELIVERED_ENVELOPE, fetch_events, fetch_task, persist_task, sample_route

TZ = "Asia/Shanghai"


class _SettingsClient:
    def __init__(self, **task_overrides: int) -> None:
        self._snapshot = PlatformSettingsSnapshot(
            revision=1,
            settings=asdict(parse_platform_settings({"task": task_overrides})),
        )

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return self._snapshot


async def _route(
    tenant: TenantContext, *, status: str = "ACTIVE", tenant_id: str | None = None
) -> uuid.UUID:
    async with tenant.session_factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant_id or tenant.tenant_id,
            platform_user_id=uuid.uuid4(),
            route=sample_route(),
        )
        await session.execute(
            sa.update(DeliveryRoute)
            .where(DeliveryRoute.id == route_id)
            .values(status=status)
        )
    return route_id


async def seed_schedule(
    tenant: TenantContext,
    *,
    intent: str = "PUSH",
    route_id: uuid.UUID | None = None,
    name: str = "A 客户周报",
    tenant_id: str | None = None,
) -> tuple[TaskSchedule, TaskScheduleRevision]:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    owner_tenant = tenant_id or tenant.tenant_id
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=uuid.uuid4(),
            tenant_id=owner_tenant,
            owner_user_id=owner,
            agent_id=agent,
            name=name,
            status="ACTIVE",
            config_version=1,
            next_fire_at=datetime.now(UTC) + timedelta(days=1),
            created_by=owner,
        )
        session.add(schedule)
        await session.flush()
        revision = TaskScheduleRevision(
            id=uuid.uuid4(),
            tenant_id=owner_tenant,
            schedule_id=schedule.id,
            revision_no=1,
            mode="REMINDER",
            instruction_raw="每天 9 点提醒我",
            normalized_goal="提醒",
            recurrence_kind="CRON",
            cron_expr="0 9 * * *",
            timezone=TZ,
            execution_spec={"mode": "REMINDER", "reminder_text": "开会"},
            delivery_intent=intent,
            delivery_route_id=route_id,
            created_by=owner,
        )
        session.add(revision)
        await session.flush()
        schedule.current_revision_id = revision.id
    return schedule, revision


async def scheduled_execution(
    tenant: TenantContext,
    schedule: TaskSchedule,
    revision: TaskScheduleRevision,
    **overrides: Any,
) -> TaskExecution:
    return await persist_task(
        tenant,
        schedule_id=schedule.id,
        schedule_revision_id=revision.id,
        trigger_type="SCHEDULED",
        input_json={"mode": "REMINDER", "reminder_text": "开会"},
        **overrides,
    )


async def finish(
    tenant: TenantContext,
    execution: TaskExecution,
    *,
    status: TerminalStatus = TerminalStatus.COMPLETED,
) -> bool:
    """走真实终态事务（状态 + 事件 + PUSH 投递意图同一事务）。"""
    async with tenant.session_factory() as session, session.begin():
        row = await session.get(TaskExecution, execution.id)
        assert row is not None
        return await write_terminal(
            session, row, TerminalChange(status=status), datetime.now(UTC)
        )


async def open_interaction(
    tenant: TenantContext,
    execution: TaskExecution,
    *,
    question: str = "请选择客户 A 还是 B",
    status: str = str(HumanInteractionStatus.OPEN),
) -> TaskHumanInteraction:
    async with tenant.session_factory() as session, session.begin():
        interaction = TaskHumanInteraction(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            execution_id=execution.id,
            owner_user_id=execution.actor_user_id,
            agent_id=execution.agent_id,
            interaction_type="AGENTIC",
            status=status,
            question=question,
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(interaction)
    return interaction


async def waiting_execution(
    tenant: TenantContext, schedule: TaskSchedule, revision: TaskScheduleRevision
) -> TaskExecution:
    return await scheduled_execution(
        tenant, schedule, revision, status=str(TaskStatus.WAITING_HUMAN)
    )


async def _deliveries(tenant: TenantContext, execution_id: uuid.UUID) -> list[TaskDelivery]:
    async with tenant.session_factory() as session, session.begin():
        rows = await session.scalars(
            select(TaskDelivery).where(TaskDelivery.execution_id == execution_id)
        )
    return list(rows)


async def _purge(tenant: TenantContext, tenant_id: str) -> None:
    """按 FK 顺序清掉另一个租户的调度/投递行（本用例自造）。"""
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.delete(TaskDeliveryAttempt).where(TaskDeliveryAttempt.tenant_id == tenant_id)
        )
        await session.execute(sa.delete(TaskDelivery).where(TaskDelivery.tenant_id == tenant_id))
        await session.execute(sa.delete(TaskEvent).where(TaskEvent.tenant_id == tenant_id))
        await session.execute(sa.delete(TaskExecution).where(TaskExecution.tenant_id == tenant_id))
        await session.execute(
            sa.update(TaskSchedule)
            .where(TaskSchedule.tenant_id == tenant_id)
            .values(current_revision_id=None)
        )
        await session.execute(
            sa.delete(TaskScheduleRevision).where(TaskScheduleRevision.tenant_id == tenant_id)
        )
        await session.execute(sa.delete(TaskSchedule).where(TaskSchedule.tenant_id == tenant_id))
        await session.execute(sa.delete(DeliveryRoute).where(DeliveryRoute.tenant_id == tenant_id))


async def _delivery_by_key(tenant: TenantContext, key: str) -> TaskDelivery | None:
    async with tenant.session_factory() as session, session.begin():
        return await session.scalar(
            select(TaskDelivery).where(
                TaskDelivery.tenant_id == tenant.tenant_id, TaskDelivery.delivery_key == key
            )
        )


async def _fetch_schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session, session.begin():
        schedule = await session.get(TaskSchedule, schedule_id)
    assert schedule is not None
    return schedule


def _loop(tenant: TenantContext, handler: Any, **kwargs: Any) -> tuple[DeliveryLoop, list[Any]]:
    calls: list[Any] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request) if callable(handler) else handler

    loop = DeliveryLoop(
        tenant.session_factory,
        HttpDeliveryClient(
            "http://im-gateway", httpx.AsyncClient(transport=httpx.MockTransport(handle))
        ),
        tenant.settings,
        **kwargs,
    )
    return loop, calls


# ---------------------------------------------------------------- 终态入队与 NONE 纪律


async def test_terminal_creates_the_final_delivery_with_the_stable_key(
    tenant: TenantContext,
) -> None:
    route_id = await _route(tenant)
    schedule, revision = await seed_schedule(tenant, route_id=route_id)
    execution = await scheduled_execution(tenant, schedule, revision)

    assert await finish(tenant, execution) is True

    deliveries = await _deliveries(tenant, execution.id)
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.delivery_key == f"execution:{execution.id}:final"
    assert delivery.delivery_kind == str(DeliveryKind.PUSH)
    assert delivery.status == "PENDING"
    assert delivery.delivery_route_id == route_id
    assert delivery.attempt_count == 0
    events = await fetch_events(tenant, delivery.id, entity_type=EntityType.DELIVERY)
    assert [event.event_type for event in events] == ["ENQUEUED"]
    # 业务终态已经写完，投递还没发出去
    assert (await fetch_task(tenant, execution.id)).status == "COMPLETED"


async def test_webchat_none_intent_creates_no_delivery_and_never_pushes(
    tenant: TenantContext,
) -> None:
    """WebChat/MSSWWEB（NONE）不建投递、不推聊天流：结果只在任务中心 pull。"""
    schedule, revision = await seed_schedule(tenant, intent="NONE", route_id=None)
    execution = await scheduled_execution(tenant, schedule, revision)

    assert await finish(tenant, execution) is True

    assert await _deliveries(tenant, execution.id) == []
    loop, calls = _loop(tenant, _handler(200))
    assert await loop.run_once() is None
    assert calls == []
    assert (await fetch_task(tenant, execution.id)).status == "COMPLETED"


def _handler(status_code: int) -> Any:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=DELIVERED_ENVELOPE)

    return handle


# ---------------------------------------------------------------- Human 问题推送


async def test_human_question_is_pushed_to_the_creation_route(tenant: TenantContext) -> None:
    route_id = await _route(tenant)
    schedule, revision = await seed_schedule(tenant, route_id=route_id)
    execution = await waiting_execution(tenant, schedule, revision)
    interaction = await open_interaction(tenant, execution)

    loop, calls = _loop(tenant, _handler(200))
    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is True
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["delivery_key"] == f"human:{interaction.id}"
    assert body["route"]["channel"] == "WECOM"
    assert body["route"]["bot_id"] == sample_route().bot_id
    assert "请选择客户 A 还是 B" in body["message"]["text"]
    assert "A 客户周报" in body["message"]["text"], "正文必须带上任务名"
    delivery = await _delivery_by_key(tenant, human_delivery_key(interaction.id))
    assert delivery is not None and delivery.status == "SENT"
    assert delivery.delivery_kind == str(DeliveryKind.HUMAN_QUESTION)
    # 投递不动业务状态：问题仍在等、执行仍 WAITING_HUMAN
    assert (await fetch_task(tenant, execution.id)).status == "WAITING_HUMAN"
    async with tenant.session_factory() as session:
        row = await session.get(TaskHumanInteraction, interaction.id)
    assert row is not None and row.status == "OPEN"


async def test_human_question_is_enqueued_once_under_repeated_ticks(tenant: TenantContext) -> None:
    """同一问题的投递键唯一：重复扫描不新增第二行、网关只收到一次。"""
    route_id = await _route(tenant)
    schedule, revision = await seed_schedule(tenant, route_id=route_id)
    execution = await waiting_execution(tenant, schedule, revision)
    interaction = await open_interaction(tenant, execution)

    loop, calls = _loop(tenant, _handler(200))
    assert await loop.run_once() is not None
    # 第二页：投递已 SENT，不会重发；问题仍 OPEN，但键已存在
    assert await loop.run_once() is None
    assert len(calls) == 1
    async with tenant.session_factory() as session:
        count = await session.scalar(
            select(sa.func.count())
            .select_from(TaskDelivery)
            .where(TaskDelivery.delivery_key == human_delivery_key(interaction.id))
        )
    assert count == 1


async def test_answered_question_delivery_is_cancelled_not_sent(tenant: TenantContext) -> None:
    """问题在发送前已被回答：投递作废，不把过期问题推到会话里。"""
    route_id = await _route(tenant)
    schedule, revision = await seed_schedule(tenant, route_id=route_id)
    execution = await waiting_execution(tenant, schedule, revision)
    interaction = await open_interaction(tenant, execution, status=str(HumanInteractionStatus.RESOLVED))
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskDelivery(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                execution_id=execution.id,
                delivery_route_id=route_id,
                delivery_kind=str(DeliveryKind.HUMAN_QUESTION),
                delivery_key=human_delivery_key(interaction.id),
                status=str(DeliveryState.PENDING),
                attempt_count=0,
            )
        )

    loop, calls = _loop(tenant, _handler(200))
    assert await loop.run_once() is None
    assert calls == []
    delivery = await _delivery_by_key(tenant, human_delivery_key(interaction.id))
    assert delivery is not None and delivery.status == "CANCELLED"


async def test_webchat_schedule_never_enqueues_a_human_question(tenant: TenantContext) -> None:
    """NONE 来源的等待只在任务中心可见：发现逻辑不建投递、不推送。"""
    schedule, revision = await seed_schedule(tenant, intent="NONE", route_id=None)
    execution = await waiting_execution(tenant, schedule, revision)
    await open_interaction(tenant, execution)

    loop, calls = _loop(tenant, _handler(200))
    assert await loop.run_once() is None
    assert calls == []
    assert await _deliveries(tenant, execution.id) == []


# ---------------------------------------------------------------- 路由失效（E-09）


async def test_broken_route_fails_delivery_and_flags_the_schedule(
    tenant: TenantContext,
) -> None:
    """网关说 bot 不存在：投递终态失败、不重试、不猜替代会话；Schedule 进入 NEEDS_ATTENTION。"""
    route_id = await _route(tenant)
    schedule, revision = await seed_schedule(tenant, route_id=route_id)
    execution = await scheduled_execution(tenant, schedule, revision)
    assert await finish(tenant, execution) is True

    def not_found(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "BOT_NOT_FOUND", "data": None})

    loop, calls = _loop(tenant, not_found)
    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    assert len(calls) == 1
    delivery = (await _deliveries(tenant, execution.id))[0]
    assert delivery.status == "FAILED"
    assert delivery.last_error_code == "BOT_NOT_FOUND"
    assert delivery.attempt_count == 1
    # 业务终态没被投递失败改写
    assert (await fetch_task(tenant, execution.id)).status == "COMPLETED"
    flagged = await _fetch_schedule(tenant, schedule.id)
    assert flagged.status == "NEEDS_ATTENTION"
    assert flagged.attention_reason == "BOT_NOT_FOUND"
    assert flagged.next_fire_at is None
    schedule_events = await fetch_events(tenant, schedule.id, entity_type=EntityType.SCHEDULE)
    assert [event.event_type for event in schedule_events] == ["NEEDS_ATTENTION"]
    assert schedule_events[0].payload_json["reason"] == "BOT_NOT_FOUND"
    # 不无限重试
    assert await loop.run_once() is None
    assert len(calls) == 1


async def test_inactive_route_is_terminal_without_calling_the_gateway(
    tenant: TenantContext,
) -> None:
    """创建时路由已停用：明确 FAILED，不向任何替代会话发送。"""
    route_id = await _route(tenant, status="DISABLED")
    schedule, revision = await seed_schedule(tenant, route_id=route_id)
    execution = await scheduled_execution(tenant, schedule, revision)
    assert await finish(tenant, execution) is True

    loop, calls = _loop(tenant, _handler(200))
    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    assert calls == [], "路由不可用时不得猜测替代会话"
    delivery = (await _deliveries(tenant, execution.id))[0]
    assert delivery.status == "FAILED"
    assert delivery.last_error_code == "DELIVERY_ROUTE_UNAVAILABLE"
    assert (await _fetch_schedule(tenant, schedule.id)).status == "NEEDS_ATTENTION"


async def test_immediate_task_route_failure_does_not_touch_any_schedule(
    tenant: TenantContext,
) -> None:
    """立即任务没有 Schedule：路由失效只落在投递记录上，不凭空改别的调度状态。"""
    execution = await persist_task(
        tenant, status="COMPLETED", finished_at=datetime.now(UTC)
    )
    async with tenant.session_factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=tenant.tenant_id,
            platform_user_id=uuid.uuid4(),
            route=sample_route(),
        )
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskDelivery(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                execution_id=execution.id,
                delivery_route_id=route_id,
                delivery_kind=str(DeliveryKind.PUSH),
                delivery_key=f"execution:{execution.id}:final",
                status=str(DeliveryState.PENDING),
                attempt_count=0,
            )
        )

    def not_found(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "BOT_NOT_FOUND", "data": None})

    loop, _calls = _loop(tenant, not_found)
    outcome = await loop.run_once()

    assert outcome is not None and outcome.sent is False
    assert (await fetch_task(tenant, execution.id)).status == "COMPLETED"


# ---------------------------------------------------------------- 崩溃/租约与公平性


async def test_crash_after_send_before_ack_retries_the_same_key(tenant: TenantContext) -> None:
    """发送后崩溃（没确认）：租约到期后按**同一个键**重投，网关幂等去重。"""
    route_id = await _route(tenant)
    schedule, revision = await seed_schedule(tenant, route_id=route_id)
    execution = await scheduled_execution(tenant, schedule, revision)
    assert await finish(tenant, execution) is True
    delivery = (await _deliveries(tenant, execution.id))[0]
    now = datetime.now(UTC)
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskDelivery)
            .where(TaskDelivery.id == delivery.id)
            .values(
                attempt_count=1,
                lease_owner="crashed-worker",
                lease_until=now - timedelta(seconds=1),
                update_time=now - timedelta(seconds=60),
            )
        )

    loop, calls = _loop(tenant, _handler(200))
    assert await loop.run_once(now=now) is not None

    assert len(calls) == 1
    assert json.loads(calls[0].content)["delivery_key"] == f"execution:{execution.id}:final"
    refreshed = (await _deliveries(tenant, execution.id))[0]
    assert refreshed.status == "SENT"
    assert refreshed.attempt_count == 2


async def test_two_tenants_both_get_delivered(tenant: TenantContext) -> None:
    """跨租户投递不会被先排队的一批挡住：第二个租户在下一轮被投递。"""
    other = f"{tenant.tenant_id}-other"
    deliveries = []
    for tenant_id, offset in ((tenant.tenant_id, 0), (other, 60)):
        schedule, revision = await seed_schedule(
            tenant,
            route_id=await _route(tenant, tenant_id=tenant_id),
            name=f"报表-{offset}",
            tenant_id=tenant_id,
        )
        execution = await scheduled_execution(
            tenant,
            schedule,
            revision,
            tenant_id=tenant_id,
            create_time=datetime.now(UTC) + timedelta(seconds=offset),
        )
        assert await finish(tenant, execution) is True
        deliveries.append(execution)

    loop, calls = _loop(tenant, _handler(200))
    try:
        assert await loop.run_once() is not None
        assert await loop.run_once() is not None
        assert len(calls) == 2
        for execution in deliveries:
            rows = await _deliveries(tenant, execution.id)
            assert [row.status for row in rows] == ["SENT"]
    finally:
        await _purge(tenant, other)
