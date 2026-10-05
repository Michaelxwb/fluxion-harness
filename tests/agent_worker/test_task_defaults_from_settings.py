"""TASK-006 · Worker 读取缝与任务默认接入（E-19）。

真实边界（业务路径不 mock）：
- 真实 PostgreSQL：`task.task_execution`/`task.task_schedule` 的既有行与新行、真实
  lease/claim 语义（`WorkerLoop.claim_one` 的 `FOR UPDATE SKIP LOCKED`）；
- 真实 Worker 应用层：`TaskService` / `SchedulerLoop` / `DeliveryLoop` / `BatchFanoutService`
  与真实的 Task/Event 落库；
- 设置源：真实 `ConsolePlatformSettingsClient`（被切断的端点 ⇒ 明确失败 + 调用方计数）或
  注入的桩快照（Console 内部端点本身由 TASK-005/E-03/E-04 覆盖）。

契约场景命令 `-k new_task_defaults` 命中本文件全部用例。
"""

from __future__ import annotations

import json
import socket
import uuid
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from muad_agent_worker.application.batch_fanout import BatchFanoutService, BatchPlan
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.application.task_service import TaskService
from muad_agent_worker.delivery.client import HttpDeliveryClient
from muad_agent_worker.delivery.service import DeliveryLoop
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_agent_worker.infrastructure.platform_settings_client import (
    PLATFORM_SETTINGS_PATH,
    ConsolePlatformSettingsClient,
)
from muad_agent_worker.scheduler.service import SKIP_MISFIRE, SchedulerLoop, ScheduleService
from muad_agent_worker.worker.service import WorkerLoop
from muad_api import AppError, render_metrics
from muad_common import SharedSettings
from muad_contracts.platform_settings import parse_platform_settings
from sqlalchemy import func, select, update

from agent_worker.conftest import TenantContext
from agent_worker.helpers import (
    FakeResolver,
    build_resolve_response,
    create_schedule_payload,
    create_task_payload,
    fetch_events,
    fetch_schedule,
    fetch_task,
    persist_task,
    sample_route,
)

INTERNAL_TOKEN = "worker-internal-token"


def _snapshot(**overrides: Any) -> PlatformSettingsSnapshot:
    """按 schema 构造一份（可局部覆盖的）合法平台设置文档快照。"""
    return PlatformSettingsSnapshot(revision=7, settings=asdict(parse_platform_settings(overrides)))


class _StubSettingsClient:
    """注入的桩快照源：记录每个边界被调用的租户。"""

    def __init__(self, snapshot: PlatformSettingsSnapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        self.calls.append(tenant_id)
        return self._snapshot


def _dead_endpoint() -> str:
    """一个刚被释放的 127.0.0.1 端口：真实连接被拒（Console 内部端点被切断）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return f"http://127.0.0.1:{port}"


def _failed_fetch_total(text: str) -> float:
    #: 只看 Worker 这一条 label（同一进程里 Runtime 侧也会记 failed）。
    for line in text.splitlines():
        if (
            line.startswith("platform_settings_fetch_total")
            and 'caller="worker"' in line
            and 'result="failed"' in line
        ):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def _body(request: httpx.Request) -> Mapping[str, Any]:
    return json.loads(request.content.decode("utf-8"))


async def _task_count(tenant: TenantContext) -> int:
    async with tenant.session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TaskExecution)
                    .where(TaskExecution.tenant_id == tenant.tenant_id)
                )
            ).scalar_one()
        )


class _CompletingExecutor:
    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        return {"status": "SUCCEEDED", "result": {"ok": True}}


# ---- E-19：新 Task 用平台默认，存量行不被改写 ----


async def test_new_task_defaults_from_platform_settings(tenant: TenantContext) -> None:
    """新 Task 冻结平台设置的 `task.max_attempts` / `task.default_deadline_hours`。"""
    existing = await persist_task(
        tenant,
        status="QUEUED",
        max_attempts=1,
        deadline_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    before = datetime.now(UTC)
    client = _StubSettingsClient(_snapshot(task={"max_attempts": 7, "default_deadline_hours": 2}))
    async with tenant.session_factory() as session:
        task = await TaskService(session, tenant.settings, settings_client=client).create(
            create_task_payload(tenant, idempotency_key="platform-defaults")
        )
        await session.commit()

    assert task.max_attempts == 7
    assert abs((task.deadline_at - (before + timedelta(hours=2))).total_seconds()) < 30, (
        task.deadline_at
    )
    assert client.calls == [tenant.tenant_id], "快照只在该任务创建边界取一次"

    # 存量行不被改写：平台默认只影响后续新建。
    unchanged = await fetch_task(tenant, existing.id)
    assert unchanged.max_attempts == 1
    assert unchanged.deadline_at == datetime(2020, 1, 1, tzinfo=UTC)


async def test_new_task_defaults_execution_does_not_rewrite_existing_rows(
    tenant: TenantContext,
) -> None:
    """真实 claim/执行既有 Task：其 `max_attempts` / `deadline_at` 一律不动。"""
    deadline = datetime(2035, 5, 5, tzinfo=UTC)
    existing = await persist_task(
        tenant,
        status="QUEUED",
        max_attempts=1,
        deadline_at=deadline,
        not_before=datetime.now(UTC) - timedelta(seconds=1),
    )
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=_CompletingExecutor(),
        instance_id="defaults-worker",
    )
    assert await worker.run_once() == existing.id, "既有 Task 必须被真实 claim 并执行"

    refreshed = await fetch_task(tenant, existing.id)
    assert refreshed.status == "COMPLETED"
    assert refreshed.max_attempts == 1, "平台默认不得改写存量行的 attempts 上限"
    assert refreshed.deadline_at == deadline, "平台默认不得改写存量行的 deadline"


async def test_new_task_defaults_fail_when_settings_source_unavailable(
    tenant: TenantContext,
) -> None:
    """设置源不可读 ⇒ 任务创建明确失败 + 调用方失败计数，不静默回退过期默认值。"""
    before_count = await _task_count(tenant)
    before_metric = _failed_fetch_total(render_metrics())
    dead_client = ConsolePlatformSettingsClient(
        _dead_endpoint(), service_token=INTERNAL_TOKEN, timeout_sec=0.5
    )
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings, settings_client=dead_client)
        with pytest.raises(AppError):
            await service.create(create_task_payload(tenant, idempotency_key="unavailable"))
        await session.rollback()
    await dead_client.aclose()

    assert await _task_count(tenant) == before_count, "失败不得留下半截 Task 行"
    assert _failed_fetch_total(render_metrics()) == before_metric + 1, (
        "取快照失败必须由调用方记 platform_settings_fetch_total{caller=worker,result=failed}"
    )


async def test_new_task_defaults_client_uses_worker_service_identity() -> None:
    """client 走真实内部端点 + 服务身份 + `X-Caller-Service: worker`，并解析快照。"""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {"revision": 3, "settings": asdict(parse_platform_settings({}))},
            },
        )

    client = ConsolePlatformSettingsClient(
        "http://console", service_token=INTERNAL_TOKEN, transport=httpx.MockTransport(handle)
    )
    snapshot = await client.fetch_snapshot(tenant_id="tenant-a")
    await client.aclose()

    assert [request.url.path for request in seen] == [PLATFORM_SETTINGS_PATH]
    headers = seen[0].headers
    assert headers["X-Internal-Service"] == INTERNAL_TOKEN
    assert headers["X-Tenant-Id"] == "tenant-a"
    assert headers["X-Caller-Service"] == "worker"
    assert snapshot.revision == 3
    assert snapshot.settings["task"]["max_attempts"] == 3


# ---- E-19：投递重试策略与文案 locale ----


async def test_new_task_defaults_apply_to_delivery_locale(tenant: TenantContext) -> None:
    """投递文案 locale 读平台设置（`locale.default_locale`），尝试次数在既有计数上自增。"""
    task = await persist_task(
        tenant,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
        delivery_route=sample_route(),
    )
    client = _StubSettingsClient(_snapshot(locale={"default_locale": "en-US"}))
    bodies: list[Mapping[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(_body(request))
        return httpx.Response(200, json={"code": "0", "data": {"accepted": True}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", http),
            tenant.settings,
            settings_client=client,
        )
        outcome = await loop.run_once(now=datetime.now(UTC))
        assert outcome is not None and outcome.sent

    assert client.calls == [tenant.tenant_id], "投递尝试边界取一次快照"
    assert bodies[0]["message"]["text"].startswith("Background task completed"), bodies[0]
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_attempts == 1
    assert refreshed.delivery_status == "SENT"


async def test_new_task_defaults_apply_to_delivery_backoff_and_attempt_cap(
    tenant: TenantContext,
) -> None:
    """退避基数与尝试上限读平台设置；**不重置已发生的尝试次数**。"""
    old = datetime.now(UTC) - timedelta(seconds=600)
    task = await persist_task(
        tenant,
        status="FAILED",
        error_code="BOOM",
        finished_at=old,
        delivery_route=sample_route(),
        delivery_attempts=3,
        update_time=old,
    )
    client = _StubSettingsClient(
        _snapshot(task={"delivery_max_attempts": 4, "delivery_backoff_base_sec": 1})
    )
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"code": "0", "data": {}})

    t0 = datetime.now(UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        loop = DeliveryLoop(
            tenant.session_factory,
            HttpDeliveryClient("http://im-gateway", http),
            tenant.settings,
            settings_client=client,
        )
        first = await loop.run_once(now=t0)
        assert first is not None and not first.sent
        # 第 4 次尝试后已达平台上限 4：不再重试（默认 5 会继续）。
        assert await loop.run_once(now=t0 + timedelta(seconds=600)) is None

    assert len(calls) == 1, "上限为 4、已发生 3 次 ⇒ 只允许再一次"
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.delivery_attempts == 4, "既有尝试次数 3 必须被保留并自增到 4"
    assert refreshed.delivery_status == "FAILED"
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["DELIVERY_FAILED"]
    assert events[0].payload_json["terminal"] is True


# ---- E-19：调度默认（misfire grace / attempts / deadline） ----


async def test_new_task_defaults_scheduler_uses_platform_values(tenant: TenantContext) -> None:
    """调度触发新建 Task 用平台默认；misfire 宽限也读平台设置。"""
    client = _StubSettingsClient(
        _snapshot(
            task={"max_attempts": 9, "default_deadline_hours": 3, "misfire_grace_sec": 20}
        )
    )
    now = datetime.now(UTC)
    schedule = await _create_schedule(tenant)
    await _set_next_fire_at(tenant, schedule.id, now - timedelta(seconds=40))

    # 宽限 20s < 40s ⇒ 判为错过，不建 Task、不消耗 resolve。
    misfire_loop = SchedulerLoop(
        tenant.session_factory,
        FakeResolver(build_resolve_response(schedule.skill_id, uuid.uuid4())),
        tenant.settings,
        settings_client=client,
    )
    assert await misfire_loop.run_once(now=now) is None
    refreshed = await fetch_schedule(tenant, schedule.id)
    assert refreshed.last_error_code == SKIP_MISFIRE
    assert await _schedule_tasks(tenant, schedule.id) == []

    # 宽限放到 120s（> 衰减后的到期偏差）⇒ 正常触发，新 Task 用平台默认。
    fresh = await _create_schedule(tenant)
    fire_moment = datetime.now(UTC)
    await _set_next_fire_at(tenant, fresh.id, fire_moment - timedelta(seconds=40))
    firing_client = _StubSettingsClient(
        _snapshot(task={"max_attempts": 9, "default_deadline_hours": 3, "misfire_grace_sec": 120})
    )
    fire_loop = SchedulerLoop(
        tenant.session_factory,
        FakeResolver(build_resolve_response(fresh.skill_id, uuid.uuid4())),
        tenant.settings,
        settings_client=firing_client,
    )
    created = await fire_loop.run_once(now=fire_moment)
    assert created is not None
    task = await fetch_task(tenant, created)
    assert task.max_attempts == 9
    assert abs((task.deadline_at - (fire_moment + timedelta(hours=3))).total_seconds()) < 30


async def _create_schedule(tenant: TenantContext) -> TaskSchedule:
    payload = create_schedule_payload(tenant)
    async with tenant.session_factory() as session:
        created = await ScheduleService(session, tenant.settings).create_schedule(
            tenant.tenant_id, payload
        )
        await session.commit()
    return created


async def _set_next_fire_at(
    tenant: TenantContext, schedule_id: uuid.UUID, next_fire_at: datetime
) -> None:
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            update(TaskSchedule)
            .where(TaskSchedule.id == schedule_id)
            .values(next_fire_at=next_fire_at)
        )


async def _schedule_tasks(tenant: TenantContext, schedule_id: uuid.UUID) -> list[TaskExecution]:
    async with tenant.session_factory() as session:
        return list(
            (
                await session.execute(
                    select(TaskExecution).where(TaskExecution.schedule_id == schedule_id)
                )
            )
            .scalars()
            .all()
        )


# ---- E-19：批量并发上限与平台容量上界取小 ----


async def test_new_task_defaults_batch_concurrency_capped_by_platform_limit(
    tenant: TenantContext,
) -> None:
    """`task.batch_max_concurrency` 与平台容量上界 `batch_platform_limit` 取小。"""
    capped = BatchFanoutService(
        tenant.session_factory,
        SharedSettings(batch_platform_limit=3),
        settings_client=_StubSettingsClient(_snapshot(task={"batch_max_concurrency": 4})),
    )
    assert capped.effective_concurrency(1000, 4) == 3
    assert capped.effective_concurrency(2, 4) == 2

    parent = await persist_task(tenant, status="RUNNING", delivery_mode="NONE")
    client = _StubSettingsClient(_snapshot(task={"batch_max_concurrency": 2}))
    service = BatchFanoutService(
        tenant.session_factory, SharedSettings(batch_platform_limit=8), settings_client=client
    )
    plan = BatchPlan.parse(
        {"items": [{"customer": "A"}, {"customer": "B"}, {"customer": "C"}], "aggregate_mode": "ALL"}
    )
    assert plan is not None
    summary = await service.fan_out(parent, plan)
    assert summary.concurrency == 2, "并发取平台默认 2"
    assert client.calls == [tenant.tenant_id]
