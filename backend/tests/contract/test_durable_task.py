"""TASK-009（Phase 5，P1 条件 FEAT）durable_task 表 + 无状态 worker。

S-09 / B-04（design §2.2 FEAT-P5-06 / §3.3 durable_task 表）。

真实边界：
- S-09：真实 DB（SQLite 恒有 + PG 门控）+ 真实 worker poll/claim/resume；
- B-04：真实开关路径——未启用（默认）worker 不启动、零副作用；
- 幂等（RISK-P5-05）：task_id PK，重复 enqueue 不产生重复执行。
- 隔离：task_id 与 tenant 均唯一（共享 PG fluxion_test 跨运行/跨测试残留）。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.services.durable_task import DurableTaskStore, DurableTaskWorker

# ---------------------------------------------------------------------------
# 双库引擎参数化（SQLite 恒有；PostgreSQL 门控）
#


def _engine_params() -> list[object]:
    params: list[object] = [pytest.param("sqlite", id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param("postgres", id="postgres"))
    return params


@pytest.fixture(params=_engine_params())
async def engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncGenerator[AsyncEngine, None]:
    kind: str = request.param
    if kind == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    else:
        dsn = os.environ.get(
            "FLUXION_POSTGRES_DSN",
            "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
        )
        engine = create_async_engine(dsn)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def store(engine: AsyncEngine) -> AsyncGenerator[DurableTaskStore, None]:
    store = DurableTaskStore(engine)
    await store.initialize()
    yield store


@pytest.fixture
def tenant() -> str:
    """每测试唯一租户（claim_next 按租户扫描，共享 PG 需隔离）。"""
    return f"tenant-{uuid.uuid4().hex[:8]}"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# S-09（启用态）：enqueue → claim → 完成/失败；有限重试；幂等
# ---------------------------------------------------------------------------


class TestS09DurableTaskWorker:
    async def test_enqueue_claim_complete_lifecycle(
        self, store: DurableTaskStore, tenant: str
    ) -> None:
        executed: list[str] = []

        async def handler(task: Any) -> None:
            executed.append(task.task_id)

        worker = DurableTaskWorker(store, handler, tenant_id=tenant, enabled=True)
        task_id = _unique("task")
        await store.enqueue(task_id, tenant, {"job": "export"})

        task = await store.get(task_id)
        assert task is not None and task.status == "pending"

        await worker.poll_once()
        assert executed == [task_id]
        task = await store.get(task_id)
        assert task is not None
        assert task.status == "done"
        assert task.done_at is not None

    async def test_failure_retries_finitely_then_failed(
        self, store: DurableTaskStore, tenant: str
    ) -> None:
        """失败有限重试：attempts 达上限 → failed 终态（无无限重试）。"""
        attempts: list[str] = []

        async def failing_handler(task: Any) -> None:
            attempts.append(task.task_id)
            raise RuntimeError("boom")

        worker = DurableTaskWorker(
            store, failing_handler, tenant_id=tenant, enabled=True, max_attempts=3
        )
        task_id = _unique("task")
        await store.enqueue(task_id, tenant, {"job": "x"})
        for _ in range(5):
            await worker.poll_once()
        assert len(attempts) == 3, f"有限重试失效：执行 {len(attempts)} 次"
        task = await store.get(task_id)
        assert task is not None and task.status == "failed"

    async def test_task_id_idempotent_no_duplicate_execution(
        self, store: DurableTaskStore, tenant: str
    ) -> None:
        """RISK-P5-05：task_id 幂等——重复 enqueue 不重复执行。"""
        executed: list[str] = []

        async def handler(task: Any) -> None:
            executed.append(task.task_id)

        worker = DurableTaskWorker(store, handler, tenant_id=tenant, enabled=True)
        idem = _unique("task")
        first = await store.enqueue(idem, tenant, {"n": 1})
        second = await store.enqueue(idem, tenant, {"n": 1})
        assert first.task_id == second.task_id

        await worker.poll_once()
        await worker.poll_once()  # 无更多 pending → no-op
        assert executed == [idem]

    async def test_claim_is_tenant_scoped(
        self, store: DurableTaskStore, tenant: str
    ) -> None:
        """tenant scope 全链路：claim 只取本租户任务。"""
        other_tenant = _unique("tenant")
        id_a = _unique("task")
        id_b = _unique("task")
        await store.enqueue(id_a, tenant, {"n": 1})
        await store.enqueue(id_b, other_tenant, {"n": 2})

        claimed = await store.claim_next(other_tenant)
        assert claimed is not None and claimed.task_id == id_b
        assert await store.claim_next(other_tenant) is None, "不应 claim 到他租户任务"

    async def test_resume_requeues_stale_claimed(
        self, store: DurableTaskStore, tenant: str
    ) -> None:
        """resume：claimed 超时未完成 → requeue 回 pending（崩溃恢复）。"""
        task_id = _unique("task")
        await store.enqueue(task_id, tenant, {"n": 1})
        await store.claim_next(tenant)  # 模拟 worker 崩溃：claimed 后无下文

        task = await store.get(task_id)
        assert task is not None and task.status == "claimed"

        requeued = await store.requeue_stale(claimed_before_seconds=0)
        # 共享 PG 上可能存在历史残留 claimed 行（跨运行）——本任务必然被 requeue
        assert requeued >= 1
        task = await store.get(task_id)
        assert task is not None and task.status == "pending"

    async def test_worker_start_enabled_runs_poll_loop(
        self, store: DurableTaskStore, tenant: str
    ) -> None:
        """启用态 start()：后台 poll 循环真实运行并完成任务。"""
        executed: list[str] = []
        done = asyncio.Event()

        async def handler(task: Any) -> None:
            executed.append(task.task_id)
            done.set()

        worker = DurableTaskWorker(
            store, handler, tenant_id=tenant, enabled=True, poll_interval=0.01
        )
        assert worker.start() is True
        try:
            task_id = _unique("task")
            await store.enqueue(task_id, tenant, {"n": 1})
            await asyncio.wait_for(done.wait(), timeout=5.0)
            # complete 落库在 handler 返回后——轮询终态
            task = None
            for _ in range(100):
                task = await store.get(task_id)
                if task is not None and task.status == "done":
                    break
                await asyncio.sleep(0.02)
        finally:
            await worker.stop()
        assert executed == [task_id]
        assert task is not None and task.status == "done"


# ---------------------------------------------------------------------------
# B-04：开关未启用（默认）→ 零副作用
# ---------------------------------------------------------------------------


class TestB04DisabledByDefault:
    async def test_disabled_worker_does_not_start(
        self, tmp_path: Path, tenant: str
    ) -> None:
        """开关关闭：start() 不启动 poll 循环、不创建后台任务。"""
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b04.db'}")
        try:
            store = DurableTaskStore(engine)
            await store.initialize()
            handler_calls: list[str] = []

            async def handler(task: Any) -> None:
                handler_calls.append(task.task_id)

            worker = DurableTaskWorker(store, handler, tenant_id=tenant)  # 默认 enabled=False
            assert worker.enabled is False
            assert worker.start() is False, "未启用的 worker 不得启动"
            task_id = _unique("task")
            await store.enqueue(task_id, tenant, {"n": 1})
            await asyncio.sleep(0.05)
            assert handler_calls == [], "未启用 worker 不得执行任务"
            task = await store.get(task_id)
            assert task is not None and task.status == "pending"
        finally:
            await engine.dispose()

    async def test_table_create_is_side_effect_free(self, tmp_path: Path) -> None:
        """表可独立建（幂等 DDL），不影响现有路径（无 worker、无消费者）。"""
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'b04b.db'}")
        try:
            store = DurableTaskStore(engine)
            await store.initialize()
            await store.initialize()  # 幂等
            async with engine.connect() as conn:
                tables = await conn.run_sync(
                    lambda sync_conn: sa_inspect(sync_conn).get_table_names()
                )
                result = await conn.execute(text("SELECT COUNT(*) FROM durable_task"))
                assert result.scalar_one() == 0
        finally:
            await engine.dispose()
        assert "durable_task" in tables
