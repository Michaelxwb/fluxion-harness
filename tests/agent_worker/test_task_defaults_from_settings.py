"""TASK-006 · Worker 读取缝与任务默认接入（E-19）；TASK-016 迁到 v2 执行链。

真实边界（业务路径不 mock）：
- 真实 PostgreSQL：`task.task_execution` 的既有行与新行、真实 lease/claim 语义
  （`WorkerLoop.claim_one` 的 `FOR UPDATE SKIP LOCKED`）、真实 Scheduler/Fire 写入；
- 真实 Worker 应用层：`TaskService` / `SchedulerLoop` / `BatchFanoutService` 与真实的
  Task/Event 落库；只把 Console 的网络取数替成协议实现（`resolve_schedule_materials`）；
- 设置源：真实 `ConsolePlatformSettingsClient`（被切断的端点 ⇒ 明确失败 + 调用方计数）或
  注入的桩快照（Console 内部端点本身由 TASK-005/E-03/E-04 覆盖）。

v2 迁移口径（TASK-016）：调度触发不再有 `ScheduleService`/`compute_next_fire_at` 的创建面，
改为种 Schedule+Revision 后走真实 `SchedulerLoop`；misfire 的事实是 FIRE 行上的
`status=SKIPPED, skip_reason=MISFIRE`，不是 Schedule 行的 `last_error_code`。旧 Task
delivery 列已不存在（投递独立成 `task_delivery`，归 TASK-022），因此原「投递 locale /
退避」两例不再以执行行列断言。
"""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from muad_agent_worker.application.batch_fanout import BatchFanoutService, BatchPlan
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.application.task_service import TaskService
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.infrastructure.platform_settings_client import (
    PLATFORM_SETTINGS_PATH,
    ConsolePlatformSettingsClient,
)
from muad_agent_worker.worker.service import WorkerLoop
from muad_api import AppError, render_metrics
from muad_common import SharedSettings
from muad_contracts.platform_settings import parse_platform_settings
from muad_contracts.snapshot import snapshot_hash
from sqlalchemy import func, select

from agent_worker.conftest import TenantContext
from agent_worker.helpers import (
    create_task_payload,
    fetch_task,
    persist_task,
)
from agent_worker.test_scheduled_v2_fire import (
    MaterialsClient,
    executions,
    fires,
    loop_for,
    materials_for,
    seed,
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
    client = _StubSettingsClient(_snapshot(task={
        "max_attempts": 7, "default_deadline_hours": 2, "execution_timeout_sec": 1800,
    }))
    async with tenant.session_factory() as session:
        task = await TaskService(session, tenant.settings, settings_client=client).create(
            create_task_payload(tenant, idempotency_key="platform-defaults")
        )
        await session.commit()

    assert task.max_attempts == 7
    assert task.execution_snapshot_json["budget"]["execution_timeout_sec"] == 1800
    assert task.snapshot_hash == snapshot_hash(task.execution_snapshot_json)
    assert abs((task.deadline_at - (before + timedelta(hours=2))).total_seconds()) < 30, (
        task.deadline_at
    )
    assert client.calls == [tenant.tenant_id], "快照只在该任务创建边界取一次"

    # 存量行不被改写：平台默认只影响后续新建。
    unchanged = await fetch_task(tenant, existing.id)
    assert unchanged.max_attempts == 1
    assert unchanged.deadline_at == datetime(2020, 1, 1, tzinfo=UTC)


async def test_new_task_writes_v2_execution_chain_columns(tenant: TenantContext) -> None:
    """立即任务按 v2 协议落库：task_type / skill_execution_mode / 投递独立成 task_delivery。"""
    from muad_agent_worker.infrastructure.models.task import TaskDelivery

    client = _StubSettingsClient(_snapshot())
    async with tenant.session_factory() as session:
        task = await TaskService(session, tenant.settings, settings_client=client).create(
            create_task_payload(tenant, idempotency_key="v2-chain")
        )
        await session.commit()
        deliveries = list(
            await session.scalars(
                select(TaskDelivery).where(TaskDelivery.execution_id == task.id)
            )
        )

    assert task.task_type == "SKILL"
    assert task.skill_execution_mode == "ASYNC"
    assert task.trigger_type == "IMMEDIATE"
    assert task.skill_id is not None and task.skill_artifact_id is not None
    assert [(row.delivery_key, row.status) for row in deliveries] == [
        (f"execution:{task.id}:final", "PENDING")
    ], "创建时的 route 落成独立投递意图，不再写执行行"


async def test_replayed_task_keeps_frozen_timeout_after_settings_change(tenant: TenantContext) -> None:
    client = _StubSettingsClient(_snapshot(task={"execution_timeout_sec": 1800}))
    payload = create_task_payload(tenant, idempotency_key="frozen-timeout-replay")
    async with tenant.session_factory() as session:
        first = await TaskService(session, tenant.settings, settings_client=client).create(payload)
        await session.commit()
    client._snapshot = _snapshot(task={"execution_timeout_sec": 600})
    async with tenant.session_factory() as session:
        replay = await TaskService(session, tenant.settings, settings_client=client).create(payload)
        await session.commit()
    assert replay.id == first.id
    assert replay.snapshot_hash == first.snapshot_hash
    assert replay.execution_snapshot_json["budget"]["execution_timeout_sec"] == 1800
    assert client.calls == [tenant.tenant_id]
    async with tenant.session_factory() as session:
        later = await TaskService(session, tenant.settings, settings_client=client).create(
            create_task_payload(tenant, idempotency_key="new-timeout-after-save")
        )
        await session.commit()
    assert later.execution_snapshot_json["budget"]["execution_timeout_sec"] == 600


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


# ---- E-19：调度默认（misfire grace / attempts / deadline） ----


async def test_new_task_defaults_scheduler_uses_platform_values(tenant: TenantContext) -> None:
    """调度触发新建 Task 用平台默认；misfire 宽限也读平台设置。"""
    now = datetime.now(UTC)
    missed = await seed(
        tenant, next_fire_at=now - timedelta(seconds=40), cron="0 * * * *", timezone="UTC"
    )
    misfire_client = _StubSettingsClient(_snapshot(task={"misfire_grace_sec": 20}))
    misfire_loop = loop_for(
        tenant, MaterialsClient(materials_for()), settings_client=misfire_client
    )

    # 宽限 20s < 40s ⇒ 判为错过：不建 Execution、不进入 Admission。
    assert await misfire_loop.run_once(now=now) is None
    [skip_fire] = await fires(tenant, missed.schedule_id)
    assert (skip_fire.status, skip_fire.skip_reason) == ("SKIPPED", "MISFIRE")
    assert await executions(tenant, missed.schedule_id) == []

    # 宽限放到 120s（> 到期偏差）⇒ 正常触发，新 Task 用平台默认。
    await seed(
        tenant, next_fire_at=now - timedelta(seconds=40), cron="0 * * * *", timezone="UTC"
    )
    firing_client = _StubSettingsClient(
        _snapshot(task={"max_attempts": 9, "default_deadline_hours": 3, "misfire_grace_sec": 120})
    )
    fire_loop = loop_for(tenant, MaterialsClient(materials_for()), settings_client=firing_client)
    created = await fire_loop.run_once(now=now)
    assert created is not None
    task = await fetch_task(tenant, created)
    assert task.max_attempts == 9
    assert task.execution_snapshot_schema_version == 4, "调度触发冻结 Snapshot v4"
    assert task.snapshot_hash == snapshot_hash(task.execution_snapshot_json)
    assert abs((task.deadline_at - (now + timedelta(hours=3))).total_seconds()) < 30


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

    moment = datetime.now(UTC)
    parent = await persist_task(
        tenant,
        status="RUNNING",
        lease_owner="worker-batch",
        lease_until=moment + timedelta(minutes=5),
    )
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


async def test_new_task_defaults_batch_children_carry_v2_task_shape(
    tenant: TenantContext,
) -> None:
    """Child fan-out 也写 v2 形状：`task_type=SKILL` + `skill_execution_mode=ASYNC`。"""
    moment = datetime.now(UTC)
    parent = await persist_task(
        tenant,
        status="RUNNING",
        lease_owner="worker-batch",
        lease_until=moment + timedelta(minutes=5),
    )
    service = BatchFanoutService(
        tenant.session_factory,
        SharedSettings(batch_platform_limit=8),
        settings_client=_StubSettingsClient(_snapshot()),
    )
    plan = BatchPlan.parse({"items": [{"customer": "A"}], "aggregate_mode": "ALL"})
    assert plan is not None
    await service.fan_out(parent, plan)

    async with tenant.session_factory() as session:
        children = list(
            (
                await session.scalars(
                    select(TaskExecution).where(TaskExecution.parent_id == parent.id)
                )
            ).all()
        )
    assert len(children) == 1
    assert children[0].item_key
    assert children[0].task_type == "SKILL"
    assert children[0].skill_execution_mode == "ASYNC"
