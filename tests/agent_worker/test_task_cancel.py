"""协作取消与取消竞态（E-06 / B-114 / RULE-worker-001）。

真实边界：取消 HTTP → 真实 PG CAS/标记 → 运行中的 Worker 检查点。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from conftest import TenantContext
from helpers import persist_task
from httpx import AsyncClient
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.service import WorkerLoop


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _row(tenant: TenantContext, task_id: uuid.UUID) -> dict[str, Any]:
    async with tenant.session_factory() as session:
        row = (
            await session.execute(
                sa.select(
                    TaskExecution.status,
                    TaskExecution.cancel_requested,
                    TaskExecution.lease_owner,
                    TaskExecution.finished_at,
                ).where(TaskExecution.id == task_id)
            )
        ).one()
    return {
        "status": row[0],
        "cancel_requested": row[1],
        "lease_owner": row[2],
        "finished_at": row[3],
    }


async def _event_types(tenant: TenantContext, task_id: uuid.UUID) -> list[str]:
    async with tenant.session_factory() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT event_type FROM task.task_event WHERE task_id = :id ORDER BY seq ASC"
                ),
                {"id": task_id},
            )
        ).all()
    return [row[0] for row in rows]


async def test_queued_cancel_is_direct_and_clears_lease(
    client: AsyncClient, tenant: TenantContext
) -> None:
    task = await persist_task(tenant)

    response = await client.post(f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant))

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["status"] == "CANCELLED"
    assert body["task_id"] == str(task.id)
    assert "CANCELLING" not in response.text, "Task 取消不得返回 CANCELLING 枚举"

    state = await _row(tenant, task.id)
    assert state["status"] == "CANCELLED"
    assert state["lease_owner"] is None
    assert state["finished_at"] is not None
    assert await _event_types(tenant, task.id) == ["CANCELLED"]


async def test_waiting_cancel_is_direct(client: AsyncClient, tenant: TenantContext) -> None:
    task = await persist_task(
        tenant, status="WAITING", not_before=datetime.now(UTC) + timedelta(hours=1)
    )

    response = await client.post(f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "CANCELLED"
    assert (await _row(tenant, task.id))["status"] == "CANCELLED"


async def test_running_cancel_keeps_running_with_flag(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """RUNNING 走协作取消：响应保持 RUNNING + cancel_requested，不假装已取消。"""
    task = await persist_task(
        tenant,
        status="RUNNING",
        lease_owner="worker-a",
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )

    response = await client.post(f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant))

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["status"] == "RUNNING", "RUNNING 取消不能提前报 CANCELLED"
    assert body["cancel_requested"] is True

    state = await _row(tenant, task.id)
    assert state["status"] == "RUNNING"
    assert state["cancel_requested"] is True
    assert await _event_types(tenant, task.id) == ["CANCEL_REQUESTED"]


async def test_repeated_cancel_is_idempotent(client: AsyncClient, tenant: TenantContext) -> None:
    task = await persist_task(tenant)

    first = await client.post(f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant))
    second = await client.post(f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant))

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"]["status"] == "CANCELLED"


async def test_terminal_task_cancel_conflicts(client: AsyncClient, tenant: TenantContext) -> None:
    """COMPLETED / FAILED 取消返回 REVISION_CONFLICT，而不是静默返回当前状态。"""
    for status in ("COMPLETED", "FAILED"):
        task = await persist_task(tenant, status=status, finished_at=datetime.now(UTC))

        response = await client.post(
            f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant)
        )

        assert response.status_code == 409, f"{status}: {response.text}"
        assert response.json()["code"] == "REVISION_CONFLICT"
        assert (await _row(tenant, task.id))["status"] == status


class _SlowExecutor:
    """执行到一半被取消为止；记录自己是否收到了取消。"""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finished = False

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        self.started.set()
        await asyncio.sleep(30)
        self.finished = True
        return {"status": "SUCCEEDED", "result": {"late": True}}


async def test_running_worker_stops_on_cancel_request(tenant: TenantContext) -> None:
    """Worker 在心跳检查点发现取消标记后停止执行，并落 CANCELLED。"""
    task = await persist_task(tenant)
    executor = _SlowExecutor()
    settings = tenant.settings.model_copy(update={"task_heartbeat_sec": 1})
    worker = WorkerLoop(
        tenant.session_factory, settings, executor=executor, instance_id="worker-a"
    )

    running = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(executor.started.wait(), timeout=5)

    async with tenant.session_factory() as session:
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == task.id)
            .values(cancel_requested=True)
        )
        await session.commit()

    await asyncio.wait_for(running, timeout=15)

    state = await _row(tenant, task.id)
    assert state["status"] == "CANCELLED", "Worker 未在检查点停止"
    assert executor.finished is False, "取消后执行器仍跑完了"
    assert await _event_types(tenant, task.id) == ["CLAIMED", "CANCELLED"]


async def test_cancel_does_not_get_overwritten_by_late_success(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """成功与取消竞态：先取消后到达的成功结果不得覆写。"""
    task = await persist_task(tenant)
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="worker-a")
    claimed = await worker._claimer.claim_one("worker-a")
    assert claimed is not None

    cancelled = await client.post(f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant))
    assert cancelled.json()["data"]["status"] == "RUNNING"

    await worker._handle_success(claimed, {"late": True}, now=datetime.now(UTC))

    state = await _row(tenant, task.id)
    assert state["status"] == "CANCELLED", "迟到的成功结果覆写了取消"
    assert state["cancel_requested"] is True
    async with tenant.session_factory() as session:
        result = (
            await session.execute(
                sa.text("SELECT result_json FROM task.task_execution WHERE id = :id"),
                {"id": task.id},
            )
        ).scalar()
    assert result is None, "被取消的任务不应留下成功结果"


async def test_e06_cancel_lifecycle_across_states(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """E-06：QUEUED 直接取消、RUNNING 协作取消、已取消幂等、其他终态冲突。"""
    queued = await persist_task(tenant)
    direct = await client.post(f"/internal/tasks/{queued.id}/cancel", headers=_headers(tenant))
    assert direct.json()["data"]["status"] == "CANCELLED"

    running = await persist_task(
        tenant,
        status="RUNNING",
        lease_owner="worker-a",
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    cooperative = await client.post(f"/internal/tasks/{running.id}/cancel", headers=_headers(tenant))
    assert cooperative.json()["data"]["status"] == "RUNNING"
    assert cooperative.json()["data"]["cancel_requested"] is True

    again = await client.post(f"/internal/tasks/{running.id}/cancel", headers=_headers(tenant))
    assert again.status_code == 200

    done = await persist_task(tenant, status="COMPLETED", finished_at=datetime.now(UTC))
    conflict = await client.post(f"/internal/tasks/{done.id}/cancel", headers=_headers(tenant))
    assert conflict.status_code == 409

    missing = await client.post(
        f"/internal/tasks/{uuid.uuid4()}/cancel", headers=_headers(tenant)
    )
    assert missing.status_code == 404


class _MemoryHints:
    def __init__(self) -> None:
        self.marked: set[uuid.UUID] = set()

    async def mark(self, task_id: uuid.UUID) -> None:
        self.marked.add(task_id)

    async def is_marked(self, task_id: uuid.UUID) -> bool:
        return task_id in self.marked

    async def aclose(self) -> None:
        return None


async def test_cancel_hint_stops_running_worker_before_next_heartbeat(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """取消 API 写 `task:cancel` hint；Worker 在两次心跳之间看到 hint 就立刻读 PG 停止。"""
    from muad_agent_worker.main import app

    task = await persist_task(tenant)
    executor = _SlowExecutor()
    hints = _MemoryHints()
    settings = tenant.settings.model_copy(
        update={"task_heartbeat_sec": 60, "task_cancel_check_sec": 1}
    )
    worker = WorkerLoop(
        tenant.session_factory, settings, executor=executor, instance_id="worker-h", cancel_hints=hints
    )
    running = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(executor.started.wait(), timeout=5)

    app.state.cancel_hints = hints
    try:
        response = await client.post(f"/internal/tasks/{task.id}/cancel", headers=_headers(tenant))
    finally:
        del app.state.cancel_hints
    assert response.json()["data"]["status"] == "RUNNING"
    assert task.id in hints.marked

    await asyncio.wait_for(running, timeout=5)
    assert (await _row(tenant, task.id))["status"] == "CANCELLED"
    assert executor.finished is False


async def test_parent_cancel_cascades_to_children(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """取消 BATCH Parent：QUEUED/WAITING Child 直接 CANCELLED，RUNNING Child 打取消标记。"""
    parent = await persist_task(tenant, status="WAITING", task_type="BATCH")
    queued = await persist_task(tenant, parent_id=parent.id, root_id=parent.id, item_key="a")
    running = await persist_task(
        tenant,
        parent_id=parent.id,
        root_id=parent.id,
        item_key="b",
        status="RUNNING",
        lease_owner="worker-x",
        lease_until=datetime.now(UTC) + timedelta(minutes=1),
    )
    done = await persist_task(
        tenant, parent_id=parent.id, root_id=parent.id, item_key="c", status="COMPLETED"
    )

    response = await client.post(f"/internal/tasks/{parent.id}/cancel", headers=_headers(tenant))

    assert response.json()["data"]["status"] == "CANCELLED"
    assert (await _row(tenant, queued.id))["status"] == "CANCELLED"
    running_state = await _row(tenant, running.id)
    assert running_state["status"] == "RUNNING"
    assert running_state["cancel_requested"] is True
    assert (await _row(tenant, done.id))["status"] == "COMPLETED", "已终态 Child 不改"
    assert await _event_types(tenant, queued.id) == ["CANCELLED"]


async def test_running_cancel_race_reports_real_status(tenant: TenantContext) -> None:
    """RUNNING 取消的 CAS 落败（任务同时完成）时回报真实终态冲突，而不是谎报 RUNNING。"""
    from muad_agent_worker.application.task_service import TaskService
    from muad_api import AppError

    task = await persist_task(tenant, status="RUNNING", lease_owner="w", lease_until=datetime.now(UTC))
    async with tenant.session_factory() as session:
        service = TaskService(session, tenant.settings)
        loaded = await service.get(tenant.tenant_id, task.id)
        assert loaded.status == "RUNNING"
        async with tenant.session_factory() as other:
            await other.execute(
                sa.update(TaskExecution).where(TaskExecution.id == task.id).values(status="COMPLETED")
            )
            await other.commit()
        original_get = service.get
        calls = {"n": 0}

        async def stale_first_get(*args: Any, **kwargs: Any) -> TaskExecution:
            calls["n"] += 1
            if calls["n"] == 1:
                return loaded
            return await original_get(*args, **kwargs)

        service.get = stale_first_get  # type: ignore[method-assign]
        try:
            await service.cancel(tenant.tenant_id, task.id)
        except AppError as exc:
            assert exc.code == "REVISION_CONFLICT"
        else:
            raise AssertionError("CAS 落败后应回读真实终态并冲突")
