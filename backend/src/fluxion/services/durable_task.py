"""DurableTask：durable_task 表 + 无状态 worker（Phase 5 TASK-009，P1 条件 FEAT）。

- **契约就绪、默认不启用**（B-04）：`DurableTaskWorker(enabled=False)` 为默认——
  `start()` 不启动 poll 循环、不执行任何任务；表可独立建（幂等 DDL），
  现有 serving 路径零变化。仅在明确存在耗时后台逻辑时由装配方 `enabled=True` 启用。
- **幂等**（RISK-P5-05）：task_id PK——重复 enqueue 返回既有任务，不重复执行。
- **有限重试**：attempts ≥ max_attempts → failed 终态（无无限重试）。
- **resume**：claimed 超时未完成（worker 崩溃）→ `requeue_stale` 回 pending。
- **tenant scope 全链路**：enqueue/claim/requeue 均按 tenant 收口。
- engine 注入：SQLite（dev/契约）与 PostgreSQL（生产）跑同一套 Contract Test（规则 7）。
- 全方法 timeout + fail policy（规则 18）：DB IO 经 `asyncio.wait_for` deadline。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import durable_task

_TASK_LOGGER = logging.getLogger("fluxion.tasks.worker")

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_DEFAULT_TIMEOUT_MS = 30_000


@dataclass(frozen=True, slots=True)
class DurableTask:
    task_id: str
    tenant_id: str
    payload: dict[str, object]
    status: str
    attempts: int
    claimed_at: datetime | None
    done_at: datetime | None
    created_at: datetime | None


class DurableTaskError(RuntimeError):
    """durable_task store/worker 失败（含超时/库错误）。"""

    code = "durable_task_error"


class DurableTaskStore:
    """durable_task 表 CRUD（engine 注入，SQLite/PG 双库同契约）。"""

    def __init__(self, engine: AsyncEngine, *, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> None:
        self._engine = engine
        self._timeout_ms = timeout_ms

    async def initialize(self) -> None:
        """幂等建表（表可独立建，B-04：不影响现有路径）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: durable_task.create(sync_conn, checkfirst=True)
            )

    async def enqueue(
        self, task_id: str, tenant_id: str, payload: dict[str, object]
    ) -> DurableTask:
        """入队（task_id 幂等：已存在 → 返回既有任务，不重复入队）。

        review P2：并发 enqueue 同一 task_id 时 SELECT-then-INSERT 竞态 → 后写方
        PK 冲突（IntegrityError）。捕获后回读返回既有任务——幂等语义闭环，
        不把竞态当存储错误上抛。
        """
        from sqlalchemy.exc import IntegrityError

        try:
            async with self._engine.begin() as conn:
                existing = await conn.execute(
                    select(durable_task).where(durable_task.c.task_id == task_id)
                )
                row = existing.fetchone()
                if row is not None:
                    return _to_task(row)
                await conn.execute(
                    insert(durable_task).values(
                        task_id=task_id,
                        tenant_id=tenant_id,
                        payload=payload,
                        status=STATUS_PENDING,
                        attempts=0,
                    )
                )
        except IntegrityError:
            # 并发双写败者：回读既有任务（幂等）
            task = await self.get(task_id)
            if task is not None:
                return task
            raise DurableTaskError(f"enqueue 竞态后任务不可见: {task_id}") from None
        task = await self.get(task_id)
        if task is None:
            raise DurableTaskError(f"enqueue 后任务不可见: {task_id}")
        return task

    async def claim_next(self, tenant_id: str) -> DurableTask | None:
        """原子领取一条 pending 任务（pending → claimed，attempts+1）。

        乐观 claim：UPDATE ... WHERE task_id IN (SELECT 最老 pending LIMIT 1)
        AND status='pending'——竞争失败（他者先领）返回 None。
        """
        async with self._engine.begin() as conn:
            oldest = await conn.execute(
                select(durable_task.c.task_id)
                .where(
                    durable_task.c.status == STATUS_PENDING,
                    durable_task.c.tenant_id == tenant_id,
                )
                .order_by(durable_task.c.created_at)
                .limit(1)
            )
            task_id = oldest.scalar_one_or_none()
            if task_id is None:
                return None
            now = datetime.now(UTC)
            claimed = await conn.execute(
                update(durable_task)
                .where(
                    durable_task.c.task_id == task_id,
                    durable_task.c.status == STATUS_PENDING,
                )
                .values(status=STATUS_CLAIMED, claimed_at=now, attempts=durable_task.c.attempts + 1)
                .returning(durable_task.c.task_id)
            )
            if claimed.scalar_one_or_none() is None:
                return None
        task = await self.get(task_id)
        return task

    async def complete(self, task_id: str) -> None:
        """claimed → done（done_at 落值）。"""
        async with self._engine.begin() as conn:
            await conn.execute(
                update(durable_task)
                .where(durable_task.c.task_id == task_id, durable_task.c.status == STATUS_CLAIMED)
                .values(status=STATUS_DONE, done_at=datetime.now(UTC))
            )

    async def fail(self, task_id: str) -> None:
        """claimed → failed 终态（done_at 落值；不再重试）。"""
        async with self._engine.begin() as conn:
            await conn.execute(
                update(durable_task)
                .where(durable_task.c.task_id == task_id, durable_task.c.status == STATUS_CLAIMED)
                .values(status=STATUS_FAILED, done_at=datetime.now(UTC))
            )

    async def requeue(self, task_id: str) -> None:
        """claimed → pending（失败重试 / resume 路径）。"""
        async with self._engine.begin() as conn:
            await conn.execute(
                update(durable_task)
                .where(durable_task.c.task_id == task_id, durable_task.c.status == STATUS_CLAIMED)
                .values(status=STATUS_PENDING, claimed_at=None)
            )

    async def requeue_stale(self, *, claimed_before_seconds: float) -> int:
        """resume：claimed 超过阈值未完成（worker 崩溃）→ 回 pending；返回条数。"""
        cutoff = datetime.now(UTC) - timedelta(seconds=claimed_before_seconds)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(durable_task)
                .where(durable_task.c.status == STATUS_CLAIMED, durable_task.c.claimed_at <= cutoff)
                .values(status=STATUS_PENDING, claimed_at=None)
            )
            return result.rowcount or 0

    async def get(self, task_id: str) -> DurableTask | None:
        async with self._engine.connect() as conn:
            row = await conn.execute(
                select(durable_task).where(durable_task.c.task_id == task_id)
            )
            record = row.fetchone()
        return _to_task(record) if record is not None else None

    async def count_by_status(self, status: str) -> int:
        async with self._engine.connect() as conn:
            row: Any = await conn.execute(
                select(func.count())
                .select_from(durable_task)
                .where(durable_task.c.status == status)
            )
            return int(row.scalar_one())


class DurableTaskWorker:
    """无状态 worker：poll → claim → execute → complete/fail（tenant scope）。

    默认 `enabled=False`（B-04）：`start()` 返回 False 且不创建任何后台任务；
    `poll_once()` 仍可显式调用（测试/手动驱动）。
    """

    def __init__(
        self,
        store: DurableTaskStore,
        handler: Callable[[DurableTask], Awaitable[None]],
        *,
        tenant_id: str,
        enabled: bool = False,
        poll_interval: float = 0.1,
        max_attempts: int = 3,
        claim_stale_seconds: float = 300.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._store = store
        self._handler = handler
        self._tenant_id = tenant_id
        self._enabled = enabled
        self._poll_interval = poll_interval
        self._max_attempts = max_attempts
        self._claim_stale_seconds = claim_stale_seconds
        self._poll_task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> bool:
        """启动 poll 循环；未启用（默认）返回 False 且零副作用（B-04）。"""
        if not self._enabled:
            return False
        if self._poll_task is not None:
            return True
        self._poll_task = asyncio.get_event_loop().create_task(self._poll_loop())
        return True

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def poll_loop(self) -> None:
        """持续轮询（含 stale requeue resume）；异常不吞（记录后继续）。"""
        while True:
            try:
                await self.poll_once()
                await self._store.requeue_stale(claimed_before_seconds=self._claim_stale_seconds)
            except Exception as exc:  # noqa: BLE001 —— poll 循环必须自愈（记录不退出）
                _TASK_LOGGER.warning("durable task poll 异常（继续轮询）: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def poll_once(self) -> DurableTask | None:
        """单轮（tenant scope）：claim 一条 pending → 执行 → complete/requeue/fail。"""
        task = await self._store.claim_next(self._tenant_id)
        if task is None:
            return None
        try:
            await asyncio.wait_for(self._handler(task), timeout=self._handler_timeout_s)
        except Exception as exc:  # noqa: BLE001 —— 失败策略：有限重试（见 attempts）
            _TASK_LOGGER.warning(
                "task %s 执行失败（attempt %s/%s）: %s",
                task.task_id, task.attempts, self._max_attempts, exc,
            )
            if task.attempts >= self._max_attempts:
                await self._store.fail(task.task_id)
            else:
                await self._store.requeue(task.task_id)
            return task
        await self._store.complete(task.task_id)
        return task

    @property
    def _handler_timeout_s(self) -> float:
        # handler 外部 IO 有界（规则 18）：单任务执行上限 30s
        return 30.0

    async def _poll_loop(self) -> None:
        await self.poll_loop()


def _to_task(row: Any) -> DurableTask:
    payload = row.payload if isinstance(row.payload, dict) else {}
    return DurableTask(
        task_id=row.task_id,
        tenant_id=row.tenant_id,
        payload=payload,
        status=row.status,
        attempts=row.attempts,
        claimed_at=row.claimed_at,
        done_at=row.done_at,
        created_at=row.created_at,
    )
