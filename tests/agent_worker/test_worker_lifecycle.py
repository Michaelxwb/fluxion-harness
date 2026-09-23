from __future__ import annotations

import pytest

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import TenantContext
from helpers import RecordingExecutor, create_task_payload, fetch_events, fetch_task, persist_task
from muad_agent_worker.application.task_service import TaskService
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import TaskExecutionError
from muad_agent_worker.scheduler.service import TASK_DEADLINE_EXCEEDED, DeadlineSweeper
from muad_agent_worker.worker.service import WorkerLoop
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

INSTANCE_ID = "test-worker:1"


class _CancellingExecutor:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def execute(self, task: TaskExecution) -> dict[str, object]:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(TaskExecution)
                .where(TaskExecution.id == task.id)
                .values(cancel_requested=True)
            )
        raise TaskExecutionError("STOP", "cancelled while running")


async def test_claim_sets_running_lease_and_completes(tenant: TenantContext) -> None:
    async with tenant.session_factory() as session:
        task = await TaskService(session, tenant.settings).create(
            create_task_payload(tenant, idempotency_key="claim-success")
        )
        await session.commit()
    executor = RecordingExecutor({"ok": True})
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor,
        instance_id=INSTANCE_ID,
    )
    claim_now = datetime.now(UTC)
    completed_id = await loop.run_once(now=claim_now)
    assert completed_id == task.id
    claimed = executor.calls[0]
    assert claimed.status == "RUNNING"
    assert claimed.attempt == 1
    assert claimed.lease_owner == INSTANCE_ID
    assert claimed.lease_until is not None
    assert claimed.lease_until > claim_now
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "COMPLETED"
    assert refreshed.result_json == {"ok": True}
    assert refreshed.finished_at is not None
    assert refreshed.lease_owner is None
    assert refreshed.started_at is not None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CREATED", "CLAIMED", "COMPLETED"]


async def test_claim_respects_not_before_and_cancel_requested(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    blocked = await persist_task(tenant, not_before=now + timedelta(minutes=5))
    cancelled = await persist_task(tenant, cancel_requested=True)
    executor = RecordingExecutor()
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor,
        instance_id=INSTANCE_ID,
    )
    assert await loop.run_once(now=now) is None
    assert executor.calls == []

    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == blocked.id)
            .values(not_before=now - timedelta(seconds=1))
        )
    claimed_id = await loop.run_once(now=now)
    assert claimed_id == blocked.id
    assert executor.calls[0].id == blocked.id
    still_queued = await fetch_task(tenant, cancelled.id)
    assert still_queued.status == "QUEUED"


async def test_failure_retries_with_exponential_backoff_then_fails(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    task = await persist_task(tenant, max_attempts=3, not_before=now)
    executor = RecordingExecutor(error=TaskExecutionError("BOOM", "kaboom"))
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor,
        instance_id=INSTANCE_ID,
    )
    await loop.run_once(now=now)
    first = await fetch_task(tenant, task.id)
    assert first.status == "QUEUED"
    assert first.attempt == 1
    assert first.error_code == "BOOM"
    assert first.error_message == "kaboom"
    assert first.not_before == now + timedelta(seconds=5)

    second_now = first.not_before + timedelta(seconds=1)
    await loop.run_once(now=second_now)
    second = await fetch_task(tenant, task.id)
    assert second.status == "QUEUED"
    assert second.attempt == 2
    assert second.not_before == second_now + timedelta(seconds=10)

    third_now = second.not_before + timedelta(seconds=1)
    await loop.run_once(now=third_now)
    failed = await fetch_task(tenant, task.id)
    assert failed.status == "FAILED"
    assert failed.attempt == 3
    assert failed.error_code == "BOOM"
    assert failed.finished_at == third_now
    assert failed.lease_owner is None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == [
        "CLAIMED",
        "RETRY",
        "CLAIMED",
        "RETRY",
        "CLAIMED",
        "FAILED",
    ]


async def test_failure_after_cancel_request_ends_cancelled(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    task = await persist_task(tenant, max_attempts=3, not_before=now)
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        _CancellingExecutor(tenant.session_factory),
        instance_id=INSTANCE_ID,
    )
    await loop.run_once(now=now)
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "CANCELLED"
    assert refreshed.cancel_requested is True
    assert refreshed.finished_at == now
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["CLAIMED", "CANCELLED"]


async def test_reclaim_expired_lease(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    task = await persist_task(
        tenant,
        status="RUNNING",
        attempt=2,
        lease_owner="dead:1",
        lease_until=now - timedelta(seconds=5),
        started_at=now - timedelta(minutes=1),
    )
    abandoned = await persist_task(
        tenant,
        status="RUNNING",
        attempt=1,
        lease_owner="dead:2",
        lease_until=now - timedelta(seconds=5),
        cancel_requested=True,
    )
    loop = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        instance_id=INSTANCE_ID,
    )
    reclaimed = await loop.reclaim_expired(now=now)
    assert reclaimed == 1
    refreshed = await fetch_task(tenant, task.id)
    assert refreshed.status == "QUEUED"
    assert refreshed.attempt == 2
    assert refreshed.lease_owner is None
    assert refreshed.lease_until is None
    events = await fetch_events(tenant, task.id)
    assert [event.event_type for event in events] == ["RECLAIMED"]
    # 已请求取消、持有者又崩溃：不 reclaim 重跑，直接收尾为 CANCELLED（不再卡到 deadline）。
    cancelled = await fetch_task(tenant, abandoned.id)
    assert cancelled.status == "CANCELLED"
    assert cancelled.finished_at == now
    assert cancelled.lease_owner is None
    assert [event.event_type for event in await fetch_events(tenant, abandoned.id)] == ["CANCELLED"]


async def test_sweep_deadlines_marks_failed(tenant: TenantContext) -> None:
    now = datetime.now(UTC)
    expired = await persist_task(tenant, deadline_at=now - timedelta(seconds=1))
    finished = await persist_task(
        tenant,
        status="COMPLETED",
        deadline_at=now - timedelta(hours=1),
        finished_at=now - timedelta(minutes=5),
    )
    sweeper = DeadlineSweeper(tenant.session_factory, tenant.settings)
    swept = await sweeper.sweep(now=now)
    assert swept == 1
    refreshed = await fetch_task(tenant, expired.id)
    assert refreshed.status == "FAILED"
    assert refreshed.error_code == TASK_DEADLINE_EXCEEDED
    assert refreshed.finished_at == now
    events = await fetch_events(tenant, expired.id)
    assert [event.event_type for event in events] == ["DEADLINE_EXCEEDED"]
    untouched = await fetch_task(tenant, finished.id)
    assert untouched.status == "COMPLETED"
    assert await fetch_events(tenant, finished.id) == []


class _FakeLoop:
    instances: list["_FakeLoop"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.started = asyncio.Event()
        self.stopped = False
        self._forever = asyncio.Event()
        _FakeLoop.instances.append(self)

    async def run_forever(self) -> None:
        self.started.set()
        try:
            await self._forever.wait()
        except asyncio.CancelledError:
            self.stopped = True
            raise


class _FakeNotifier:
    def __init__(self) -> None:
        self.closed = False

    async def notify(self) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


async def test_b122_lifespan_runs_and_stops_all_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 lifespan 启动三条后台循环；退出时全部取消并等待，无悬挂任务。"""
    import muad_agent_worker.main as main_module

    _FakeLoop.instances = []
    notifier = _FakeNotifier()
    monkeypatch.setattr(main_module, "WorkerLoop", _FakeLoop)
    monkeypatch.setattr(main_module, "SchedulerLoop", _FakeLoop)
    monkeypatch.setattr(main_module, "DeliveryLoop", _FakeLoop)

    async def fake_notifier(_: str | None) -> _FakeNotifier:
        return notifier

    monkeypatch.setattr(main_module, "create_wakeup_notifier", fake_notifier)
    before = asyncio.all_tasks()

    async with main_module.app.router.lifespan_context(main_module.app):
        await asyncio.gather(*(loop.started.wait() for loop in _FakeLoop.instances))
        assert len(_FakeLoop.instances) == 3, "Worker/Scheduler/Delivery 各自独立循环"
        assert all(not loop.stopped for loop in _FakeLoop.instances)
        assert main_module.app.state.wakeup_notifier is notifier

    assert all(loop.stopped for loop in _FakeLoop.instances), "退出必须取消并等待后台任务"
    assert notifier.closed is True
    leftover = [task for task in asyncio.all_tasks() - before if not task.done()]
    assert leftover == [], f"存在悬挂后台任务: {leftover}"


async def test_b122_lifespan_fails_fast_when_migrations_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """依赖未就绪（迁移目录缺失）时 lifespan 显式失败，不启动后台循环。"""
    import muad_agent_worker.main as main_module
    from muad_common import SharedSettings

    monkeypatch.setattr(
        main_module, "SharedSettings", lambda: SharedSettings(migrations_dir="/nonexistent/versions")
    )
    _FakeLoop.instances = []
    monkeypatch.setattr(main_module, "WorkerLoop", _FakeLoop)
    monkeypatch.setattr(main_module, "SchedulerLoop", _FakeLoop)
    monkeypatch.setattr(main_module, "DeliveryLoop", _FakeLoop)

    raised = False
    try:
        async with main_module.app.router.lifespan_context(main_module.app):
            pass
    except Exception:
        raised = True
    assert raised is True, "迁移缺失必须显式失败"
    assert _FakeLoop.instances == [], "失败时不得启动后台循环"


async def test_b122_readyz_uses_shared_probes_for_database_and_artifact_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """就绪探针来自 api-kit 共享原语：DB + Artifact/NFS 存储；Redis 只作 hint 详情。"""
    import muad_agent_worker.main as main_module
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
    assert health.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["data"]["status"] == "ready"

    monkeypatch.setenv("ARTIFACT_ROOT", "/nonexistent/artifact-root")
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        degraded = await client.get("/readyz")
    assert degraded.status_code == 503
    assert "artifact_storage" in degraded.json()["data"]["failed"]


async def test_b122_task_model_has_no_pod_or_user_binding() -> None:
    """Task/Schedule 不绑定 Pod 或用户会话：只有 lease_owner（实例租约）。"""
    from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule

    task_columns = set(TaskExecution.__table__.columns.keys())
    assert "lease_owner" in task_columns
    for forbidden in ("pod", "pod_name", "hostname", "node_name", "session_id"):
        assert forbidden not in task_columns
    assert "actor_user_id" in task_columns and "tenant_id" in task_columns
    assert "lease_owner" not in set(TaskSchedule.__table__.columns.keys())


async def test_wakeup_hint_wakes_idle_listener_early(tenant: TenantContext) -> None:
    """`task:wakeup` 有订阅方：真实 Redis 发布后，空闲 Worker 的 poll 等待提前结束。"""
    import asyncio
    import time

    from muad_agent_worker.infrastructure.wakeup_hint import (
        RedisWakeupListener,
        create_wakeup_listener,
        create_wakeup_notifier,
    )

    listener = await create_wakeup_listener(tenant.settings.redis_url)
    notifier = await create_wakeup_notifier(tenant.settings.redis_url)
    if not isinstance(listener, RedisWakeupListener):
        await listener.aclose()
        await notifier.aclose()
        raise AssertionError("REDIS_URL 未配置或不可达：该用例需要真实 Redis")
    try:
        await asyncio.sleep(0.3)  # 等订阅建立
        started = time.monotonic()
        waiter = asyncio.create_task(listener.wait(10))
        await asyncio.sleep(0.1)
        await notifier.notify()
        await asyncio.wait_for(waiter, timeout=5)
        assert time.monotonic() - started < 5
    finally:
        await listener.aclose()
        await notifier.aclose()
