from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import TenantContext
from helpers import create_task_payload, fetch_events, fetch_task, persist_task, sample_route
from httpx import AsyncClient
from muad_agent_worker.application.task_service import CANCEL_PENDING_STATUS, TaskService
from muad_agent_worker.infrastructure.models.task import DeliveryRoute, TaskExecution
from muad_agent_worker.main import app
from muad_api import AppError
from sqlalchemy import func, select


async def test_create_dedupes_by_idempotency_key(tenant: TenantContext) -> None:
    payload = create_task_payload(tenant, idempotency_key="dedupe-key")
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        first = await service.create(payload)
        second = await service.create(payload)
        await session.commit()
    assert first.id == second.id
    assert await _task_count(tenant, tenant.tenant_id) == 1
    events = await fetch_events(tenant, first.id)
    assert [event.event_type for event in events] == ["CREATED"]


async def test_create_reuses_delivery_route(tenant: TenantContext) -> None:
    route = sample_route()
    actor = uuid.uuid4()
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        first = await service.create(
            create_task_payload(
                tenant, idempotency_key="route-1", delivery_route=route, actor_user_id=actor
            )
        )
        second = await service.create(
            create_task_payload(
                tenant, idempotency_key="route-2", delivery_route=route, actor_user_id=actor
            )
        )
        await session.commit()
    assert first.delivery_route_id == second.delivery_route_id
    async with tenant.session_factory() as session, session.begin():
        routes = (
            (await session.execute(select(DeliveryRoute).where(DeliveryRoute.tenant_id == tenant.tenant_id)))
            .scalars()
            .all()
        )
    assert len(routes) == 1
    assert routes[0].route_hash.startswith("sha256:")
    assert routes[0].platform_user_id is not None


async def test_create_initial_fields(tenant: TenantContext) -> None:
    before = datetime.now(UTC)
    payload = create_task_payload(tenant, idempotency_key="initial-fields")
    async with tenant.session_factory() as session:
        task = await TaskService(session, tenant.settings).create(payload)
        await session.commit()
    assert task.status == "QUEUED"
    assert task.trigger_type == "IMMEDIATE"
    assert task.execution_mode == "ASYNC"
    assert task.attempt == 0
    assert task.max_attempts == tenant.settings.task_max_attempts
    assert task.delivery_key == f"task:{task.id}:final"
    assert task.delivery_status == "PENDING"
    assert task.cancel_requested is False
    assert task.not_before >= before - timedelta(seconds=5)
    deadline = task.deadline_at
    expected_deadline = before + timedelta(hours=tenant.settings.task_default_deadline_hours)
    assert abs((deadline - expected_deadline).total_seconds()) < 30
    assert task.execution_snapshot_json == payload.execution_snapshot
    assert task.snapshot_hash == payload.snapshot_hash


async def test_create_without_route_marks_delivery_none(tenant: TenantContext) -> None:
    payload = create_task_payload(
        tenant,
        idempotency_key="no-route",
        with_route=False,
        delivery_mode="NONE",
    )
    async with tenant.session_factory() as session:
        task = await TaskService(session, tenant.settings).create(payload)
        await session.commit()
    assert task.delivery_mode == "NONE"
    assert task.delivery_status == "NONE"
    assert task.delivery_route_id is None


async def test_cancel_queued_task(tenant: TenantContext) -> None:
    payload = create_task_payload(tenant, idempotency_key="cancel-queued")
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        task = await service.create(payload)
        status = await service.cancel(tenant.tenant_id, task.id)
        await session.commit()
    assert status == "CANCELLED"
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "CANCELLED"
    assert refreshed.cancel_requested is True
    assert refreshed.finished_at is not None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CREATED", "CANCELLED"]

    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        repeated = await service.cancel(tenant.tenant_id, task.id)
        await session.commit()
    assert repeated == "CANCELLED"
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CREATED", "CANCELLED"]


async def test_cancel_running_task_is_cooperative(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    task = await persist_task(
        tenant,
        status="RUNNING",
        lease_owner="other:1",
        lease_until=now + timedelta(seconds=60),
    )
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        status = await service.cancel(tenant.tenant_id, task.id)
        await session.commit()
    assert status == CANCEL_PENDING_STATUS
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "RUNNING"
    assert refreshed.cancel_requested is True
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CANCEL_REQUESTED"]


async def test_cancel_terminal_task_is_noop(tenant: TenantContext) -> None:
    task = await persist_task(tenant, status="COMPLETED", finished_at=datetime.now(UTC))
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        status = await service.cancel(tenant.tenant_id, task.id)
        await session.commit()
    assert status == "COMPLETED"
    assert await fetch_events(tenant, task.id) == []


async def test_get_unknown_task_raises(tenant: TenantContext) -> None:
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        with pytest.raises(AppError) as exc_info:
            await service.get(tenant.tenant_id, uuid.uuid4())
    assert exc_info.value.code == "COMMON_NOT_FOUND"


async def test_list_filters_and_pagination(tenant: TenantContext) -> None:
    agent_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    now = datetime.now(UTC)
    await persist_task(
        tenant,
        agent_id=agent_id,
        actor_user_id=actor_id,
        status="QUEUED",
        trigger_type="IMMEDIATE",
        now=now - timedelta(minutes=3),
        create_time=now - timedelta(minutes=3),
    )
    await persist_task(
        tenant,
        agent_id=agent_id,
        actor_user_id=uuid.uuid4(),
        status="FAILED",
        trigger_type="SCHEDULED",
        now=now - timedelta(minutes=2),
        create_time=now - timedelta(minutes=2),
    )
    await persist_task(
        tenant,
        agent_id=uuid.uuid4(),
        actor_user_id=actor_id,
        status="COMPLETED",
        trigger_type="SCHEDULED",
        now=now - timedelta(minutes=1),
        create_time=now - timedelta(minutes=1),
    )
    async with tenant.session_factory() as session, session.begin():
        service = TaskService(session, tenant.settings)
        items, total = await service.list(tenant.tenant_id)
        assert total == 3
        assert len(items) == 3

        filtered, filtered_total = await service.list(tenant.tenant_id, agent_id=agent_id)
        assert filtered_total == 2
        assert all(task.agent_id == agent_id for task in filtered)

        by_status, by_status_total = await service.list(tenant.tenant_id, status="FAILED")
        assert by_status_total == 1
        assert by_status[0].status == "FAILED"

        by_trigger, by_trigger_total = await service.list(tenant.tenant_id, trigger_type="SCHEDULED")
        assert by_trigger_total == 2

        by_actor, by_actor_total = await service.list(tenant.tenant_id, actor_user_id=actor_id)
        assert by_actor_total == 2

        ranged, ranged_total = await service.list(
            tenant.tenant_id,
            start_time=now - timedelta(minutes=2, seconds=30),
            end_time=now,
        )
        assert ranged_total == 2

        paginated, paginated_total = await service.list(tenant.tenant_id, page=1, page_size=2)
        assert paginated_total == 3
        assert len(paginated) == 2
        assert paginated[0].create_time >= paginated[1].create_time

        second_page, _ = await service.list(tenant.tenant_id, page=2, page_size=2)
        assert len(second_page) == 1


async def test_snapshot_required_keys_are_enforced(tenant: TenantContext) -> None:
    """快照缺少必需版本键时拒绝创建（设计 §3.3.3）。"""
    payload = create_task_payload(tenant, idempotency_key="snap-missing")
    incomplete = payload.model_copy(
        update={"execution_snapshot": {"schema_version": 1, "agent": {"key": "agent"}}}
    )
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        with pytest.raises(AppError) as excinfo:
            await service.create(incomplete)
    assert excinfo.value.code == "COMMON_VALIDATION_ERROR"
    assert await _task_count(tenant, tenant.tenant_id) == 0


async def test_snapshot_with_secret_is_rejected(tenant: TenantContext) -> None:
    """快照不得携带密钥。"""
    payload = create_task_payload(tenant, idempotency_key="snap-secret")
    tainted = {
        **payload.execution_snapshot,
        "model": {"key": "model", "api_key": "sk-live-123"},
    }
    leaking = payload.model_copy(update={"execution_snapshot": tainted})
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        with pytest.raises(AppError) as excinfo:
            await service.create(leaking)
    assert excinfo.value.code == "COMMON_VALIDATION_ERROR"
    assert await _task_count(tenant, tenant.tenant_id) == 0


async def test_snapshot_is_stored_verbatim(tenant: TenantContext) -> None:
    """合法快照按原样冻结落库，不被改写。"""
    payload = create_task_payload(tenant, idempotency_key="snap-frozen")
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        created = await service.create(payload)
        await session.commit()
    task = await fetch_task(tenant, created.id)
    assert task.execution_snapshot_json == payload.execution_snapshot
    assert task.snapshot_hash == payload.snapshot_hash
    assert task.execution_snapshot_schema_version == payload.execution_snapshot_schema_version


class _FailingNotifier:
    def __init__(self) -> None:
        self.calls = 0

    async def notify(self) -> None:
        self.calls += 1
        raise RuntimeError("redis unavailable")


async def test_wakeup_hint_failure_does_not_lose_task(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """wakeup hint 失败不影响提交：任务仍然落库，可由 PG 扫描推进。"""
    notifier = _FailingNotifier()
    app.state.wakeup_notifier = notifier
    try:
        payload = create_task_payload(tenant, idempotency_key="hint-fail").model_dump(mode="json")
        response = await client.post(
            "/internal/tasks", json=payload, headers={"X-Tenant-Id": tenant.tenant_id}
        )
        assert response.status_code == 200, response.text
        assert notifier.calls >= 1, "提交后应尝试发布 wakeup hint"
        assert await _task_count(tenant, tenant.tenant_id) == 1
    finally:
        app.state.wakeup_notifier = None


async def _task_count(tenant: TenantContext, tenant_id: str) -> int:
    async with tenant.session_factory() as session, session.begin():
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TaskExecution)
                    .where(TaskExecution.tenant_id == tenant_id)
                )
            ).scalar_one()
        )
