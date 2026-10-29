"""进程级事件批量提交：跨 Run 合并锁/序号/INSERT/COMMIT。

背景（2026-10-27 Stage 1 复测）：每事件一次事务（`database_now` + 锁 + 分配 seq + INSERT +
COMMIT）把 PG 打到 90/100 连接、idle-in-tx 88、324 commits/s。这里把同一进程内多个 Run 的
事件在 **10ms / 32 条**窗口内合并成一批：

- 按 conversation_id / run_id 排序加锁（与单条 `lock_run_source` 同序，避免死锁）；
- 锁后取一次 PG 时间，逐 Run 校验 lease/epoch/deadline；
- **Plan B 故障隔离**：某个 Run 失效/会话缺失只失败它自己的 Future，其余事件照常提交；
- 每会话一次 `last_seq += N RETURNING` 分配 seq 区间，事件顺序不变；
- 一次 `INSERT` + 一次 `COMMIT`；数据库错误才整批回滚。

调用方仍是 `RunService._persist_event`：`await batcher.submit(entry)` 返回 canonical seq；
提交成功后才执行该条目的 `on_commit`（tail 唤醒），失败则把异常交给等待的 Run。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common.db_counters import db_stage_scope
from muad_contracts import canonical_json
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.async_tools import RunContinuation
from ..infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from ..metrics import (
    EVENT_BATCH_CLOCK_SECONDS,
    EVENT_BATCH_COMMIT_SECONDS,
    EVENT_BATCH_CONTINUATION_LOCK_SECONDS,
    EVENT_BATCH_CONVERSATION_BUCKET,
    EVENT_BATCH_CONVERSATION_LOCK_SECONDS,
    EVENT_BATCH_CONVERSATIONS,
    EVENT_BATCH_CONVERSATIONS_TOTAL,
    EVENT_BATCH_EVENTS_TOTAL,
    EVENT_BATCH_FLUSH_SECONDS,
    EVENT_BATCH_FLUSHES_TOTAL,
    EVENT_BATCH_INSERT_SECONDS,
    EVENT_BATCH_LOCK_SECONDS,
    EVENT_BATCH_QUEUE_DEPTH,
    EVENT_BATCH_QUEUE_SECONDS,
    EVENT_BATCH_RUN_BUCKET,
    EVENT_BATCH_RUN_LOCK_SECONDS,
    EVENT_BATCH_RUNS,
    EVENT_BATCH_RUNS_TOTAL,
    EVENT_BATCH_SEQ_SECONDS,
    EVENT_BATCH_SIZE,
    EVENT_BATCH_WAIT_SECONDS,
    record_counter,
    record_gauge,
    record_latency,
)
from .async_tools.continuation_service import (
    ExecutionIdentity,
    ExecutionLeaseLost,
    ensure_execution,
)

logger = logging.getLogger(__name__)

#: 一批最多合并的事件数：过大让首事件等待变长，过小摊薄不了事务。
DEFAULT_MAX_BATCH = 32
#: 批窗口：后续事件最多等这么久；首事件/首 delta 走 flush_now 不受影响。
DEFAULT_FLUSH_INTERVAL_SEC = 0.010
#: 有界队列：满了让提交方等待（背压），绝不丢事件。
DEFAULT_QUEUE_SIZE = 2048


@dataclass(slots=True)
class EventBatchEntry:
    """一条待提交事件 + 它所属 Run 的预期执行身份。"""

    tenant_id: str
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    identity: ExecutionIdentity
    event_type: str
    payload: dict[str, Any]
    submission_id: uuid.UUID | None = None
    stream_type: str | None = None
    artifact_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    #: true = 入队后立刻结束当前批（首事件/首 delta 不等待窗口）。
    flush_now: bool = False
    #: 提交成功后执行（tail 唤醒）；失败不执行，且失败不影响事实写入。
    on_commit: Callable[[], Awaitable[None]] | None = None


def _batch_bucket(size: int) -> str:
    if size <= 1:
        return "1"
    if size <= 3:
        return "2-3"
    return "4+"


@dataclass(slots=True)
class _Pending:
    entry: EventBatchEntry
    future: asyncio.Future[int]
    seq: int | None = None
    error: BaseException | None = None
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    skip_insert: bool = False
    enqueued_at: float = field(default_factory=time.monotonic)


class EventBatcher:
    """进程/事件循环级的跨 Run 批量写入器；`submit` 的 Future 在 commit 后完成。"""

    def __init__(
        self,
        session_factory: Callable[[], async_sessionmaker[AsyncSession]],
        *,
        max_batch: int = DEFAULT_MAX_BATCH,
        flush_interval_sec: float = DEFAULT_FLUSH_INTERVAL_SEC,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        if max_batch < 1:
            raise ValueError("max_batch must be positive")
        if flush_interval_sec <= 0:
            raise ValueError("flush_interval_sec must be positive")
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self._session_factory = session_factory
        self._max_batch = max_batch
        self._flush_interval_sec = flush_interval_sec
        self._queue: asyncio.Queue[_Pending] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def submit(self, entry: EventBatchEntry) -> int:
        """入队并等待本批提交；队列满时等待（背压），关闭后显式失败。"""
        if self._closed:
            raise RuntimeError("event batcher is closed")
        future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        await self._queue.put(_Pending(entry=entry, future=future))
        self._ensure_task()
        return await future

    def _ensure_task(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="event-batcher")

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            batch = [first]
            deadline = asyncio.get_running_loop().time() + self._flush_interval_sec
            while len(batch) < self._max_batch:
                timeout = deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), timeout))
                    if batch[-1].entry.flush_now:
                        break
                except TimeoutError:
                    break
            await self._flush(batch)

    @db_stage_scope("event")
    async def _flush(self, batch: Sequence[_Pending]) -> None:
        started = time.monotonic()
        run_count = len({p.entry.run_id for p in batch})
        conversation_count = len({p.entry.conversation_id for p in batch})
        record_gauge(EVENT_BATCH_SIZE, len(batch))
        record_gauge(EVENT_BATCH_RUNS, run_count)
        record_gauge(EVENT_BATCH_CONVERSATIONS, conversation_count)
        record_counter(EVENT_BATCH_FLUSHES_TOTAL)
        record_counter(EVENT_BATCH_RUNS_TOTAL, run_count)
        record_counter(EVENT_BATCH_CONVERSATIONS_TOTAL, conversation_count)
        record_counter(EVENT_BATCH_RUN_BUCKET, 1, {"bucket": _batch_bucket(run_count)})
        record_counter(
            EVENT_BATCH_CONVERSATION_BUCKET,
            1,
            {"bucket": _batch_bucket(conversation_count)},
        )
        record_gauge(EVENT_BATCH_QUEUE_DEPTH, self._queue.qsize())
        committed: list[_Pending] = []
        try:
            async with self._session_factory()() as session:
                lock_started = time.monotonic()
                present, run_rows = await self._lock_sources(session, batch)
                record_latency(EVENT_BATCH_LOCK_SECONDS, time.monotonic() - lock_started)
                clock_started = time.monotonic()
                now = await database_now(session)
                record_latency(EVENT_BATCH_CLOCK_SECONDS, time.monotonic() - clock_started)
                ready = self._validate(batch, present, run_rows, now)
                if not ready:
                    self._finish(batch)
                    return
                ready = await self._resolve_idempotent(session, ready)
                seq_started = time.monotonic()
                await self._assign_sequences(session, ready)
                record_latency(EVENT_BATCH_SEQ_SECONDS, time.monotonic() - seq_started)
                session.add_all(
                    [
                        CanonicalEvent(
                            id=pending.event_id,
                            tenant_id=pending.entry.tenant_id,
                            conversation_id=pending.entry.conversation_id,
                            run_id=pending.entry.run_id,
                            seq=pending.seq,
                            submission_id=pending.entry.submission_id,
                            stream_type=pending.entry.stream_type,
                            event_type=pending.entry.event_type,
                            payload_json=pending.entry.payload,
                            artifact_id=pending.entry.artifact_id,
                            source_event_id=pending.entry.source_event_id,
                        )
                        for pending in ready
                        if pending.seq is not None and not pending.skip_insert
                    ]
                )
                insert_started = time.monotonic()
                await session.flush()
                record_latency(EVENT_BATCH_INSERT_SECONDS, time.monotonic() - insert_started)
                commit_started = time.monotonic()
                await session.commit()
                record_latency(EVENT_BATCH_COMMIT_SECONDS, time.monotonic() - commit_started)
                committed = ready
        except asyncio.CancelledError:
            for pending in batch:
                if pending.error is None and pending.future.done() is False:
                    pending.error = RuntimeError("event batcher closed")
            self._finish(batch)
            raise
        except BaseException as exc:  # noqa: BLE001 —— DB/连接错误整批失败，等待方显式收到
            for pending in batch:
                if pending.error is None and pending.future.done() is False:
                    pending.error = exc
            logger.warning(
                "event_batch_flush_failed",
                extra={"batch_size": len(batch), "error_type": type(exc).__name__},
            )
        finally:
            self._finish(batch)
            record_latency(EVENT_BATCH_FLUSH_SECONDS, time.monotonic() - started)
        record_counter(EVENT_BATCH_EVENTS_TOTAL, len(committed))
        for pending in committed:
            record_latency(
                EVENT_BATCH_WAIT_SECONDS, time.monotonic() - pending.enqueued_at
            )
            record_latency(
                EVENT_BATCH_QUEUE_SECONDS, started - pending.enqueued_at
            )
        for pending in committed:
            await self._wake(pending)

    async def _lock_sources(
        self, session: AsyncSession, batch: Sequence[_Pending]
    ) -> tuple[set[tuple[str, uuid.UUID]], dict[uuid.UUID, RunRecord]]:
        """按 conversation → run → continuation 的固定顺序锁住本批全部来源。"""
        conversation_ids = sorted({p.entry.conversation_id for p in batch})
        tenant_ids = sorted({p.entry.tenant_id for p in batch})
        run_ids = sorted({p.entry.run_id for p in batch})
        conversation_started = time.monotonic()
        conversation_rows = (
            await session.execute(
                sa.select(Conversation.tenant_id, Conversation.id)
                .where(
                    Conversation.id.in_(conversation_ids),
                    Conversation.tenant_id.in_(tenant_ids),
                    Conversation.is_deleted.is_(False),
                )
                .order_by(Conversation.id)
                .with_for_update()
            )
        ).all()
        record_latency(
            EVENT_BATCH_CONVERSATION_LOCK_SECONDS,
            time.monotonic() - conversation_started,
        )
        present = {
            (str(tenant_id), cast_uuid(conversation_id))
            for tenant_id, conversation_id in conversation_rows
        }
        run_started = time.monotonic()
        run_rows = (
            await session.execute(
                sa.select(RunRecord)
                .where(RunRecord.id.in_(run_ids), RunRecord.is_deleted.is_(False))
                .order_by(RunRecord.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
        record_latency(EVENT_BATCH_RUN_LOCK_SECONDS, time.monotonic() - run_started)
        runs = {run.id: run for run in run_rows}
        continuation_started = time.monotonic()
        await session.execute(
            sa.select(RunContinuation.run_id)
            .where(
                RunContinuation.run_id.in_(run_ids),
                RunContinuation.is_deleted.is_(False),
            )
            .order_by(RunContinuation.run_id)
            .with_for_update()
        )
        record_latency(
            EVENT_BATCH_CONTINUATION_LOCK_SECONDS,
            time.monotonic() - continuation_started,
        )
        return present, runs

    def _validate(
        self,
        batch: Sequence[_Pending],
        present: set[tuple[str, uuid.UUID]],
        runs: dict[uuid.UUID, RunRecord],
        now: datetime,
    ) -> list[_Pending]:
        """Plan B：失效 Run/缺失会话只失败自己的 Future；返回可提交条目。"""
        ready: list[_Pending] = []
        for pending in batch:
            entry = pending.entry
            run = runs.get(entry.run_id)
            if run is None or (entry.tenant_id, entry.conversation_id) not in present:
                pending.error = ExecutionLeaseLost("event source missing")
                continue
            if run.tenant_id != entry.tenant_id or run.conversation_id != entry.conversation_id:
                pending.error = ExecutionLeaseLost("event source mismatch")
                continue
            try:
                ensure_execution(run, entry.identity, now)
            except ExecutionLeaseLost as exc:
                pending.error = exc
                continue
            ready.append(pending)
        return ready

    async def _resolve_idempotent(
        self, session: AsyncSession, ready: Sequence[_Pending]
    ) -> list[_Pending]:
        """`source_event_id` 去重：已存在且一致 → 复用 seq；不一致/批内重复 → 只失败该条。"""
        source_ids = {
            pending.entry.source_event_id
            for pending in ready
            if pending.entry.source_event_id is not None
        }
        existing: dict[tuple[str, uuid.UUID], CanonicalEvent] = {}
        if source_ids:
            rows = (
                await session.execute(
                    sa.select(CanonicalEvent).where(
                        CanonicalEvent.source_event_id.in_(source_ids),
                        CanonicalEvent.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
            existing = {
                (row.tenant_id, cast_uuid(row.source_event_id)): row
                for row in rows
                if row.source_event_id is not None
            }
        seen: set[tuple[str, uuid.UUID]] = set()
        remaining: list[_Pending] = []
        for pending in ready:
            source_id = pending.entry.source_event_id
            if source_id is None:
                remaining.append(pending)
                continue
            key = (pending.entry.tenant_id, source_id)
            row = existing.get(key)
            if row is not None:
                if (
                    row.run_id != pending.entry.run_id
                    or row.event_type != pending.entry.event_type
                    or canonical_json(row.payload_json) != canonical_json(pending.entry.payload)
                ):
                    pending.error = AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
                    continue
                pending.seq = int(row.seq)
                pending.skip_insert = True
                remaining.append(pending)
                continue
            if key in seen:
                pending.error = AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
                continue
            seen.add(key)
            remaining.append(pending)
        return remaining

    async def _assign_sequences(self, session: AsyncSession, ready: Sequence[_Pending]) -> None:
        grouped: dict[tuple[str, uuid.UUID], list[_Pending]] = {}
        for pending in ready:
            if pending.seq is not None:
                continue
            grouped.setdefault(
                (pending.entry.tenant_id, pending.entry.conversation_id), []
            ).append(pending)
        for (tenant_id, conversation_id), group in grouped.items():
            last = await session.scalar(
                sa.update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                    Conversation.is_deleted.is_(False),
                )
                .values(last_seq=Conversation.last_seq + len(group), update_time=sa.func.now())
                .returning(Conversation.last_seq)
            )
            if last is None:
                for pending in group:
                    pending.error = ExecutionLeaseLost("conversation missing during allocate")
                continue
            start = int(last) - len(group) + 1
            for offset, pending in enumerate(group):
                pending.seq = start + offset

    def _finish(self, batch: Sequence[_Pending]) -> None:
        for pending in batch:
            if pending.future.done():
                continue
            if pending.error is not None:
                pending.future.set_exception(pending.error)
            elif pending.seq is not None:
                pending.future.set_result(pending.seq)
            else:
                pending.future.set_exception(RuntimeError("event batch produced no sequence"))

    async def _wake(self, pending: _Pending) -> None:
        if pending.entry.on_commit is None:
            return
        try:
            await pending.entry.on_commit()
        except Exception:  # noqa: BLE001 —— 唤醒只是加速，失败降级为 PG 补查
            logger.warning("event_batch_wake_failed", extra={"run_id": str(pending.entry.run_id)})

    async def aclose(self) -> None:
        """测试/进程收尾：取消写入任务并让未完成提交显式失败。"""
        self._closed = True
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        while not self._queue.empty():
            pending = self._queue.get_nowait()
            if not pending.future.done():
                pending.future.set_exception(RuntimeError("event batcher closed"))

    def cancel(self) -> None:
        """同步收尾（测试 fixture）：只取消任务，不等待；不用于生产 shutdown。"""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            self._task = None


def cast_uuid(value: Any) -> uuid.UUID:
    """数据库驱动在部分查询形态下回传 str；统一成 UUID 再比较。"""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


_batchers: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, EventBatcher] = (
    weakref.WeakKeyDictionary()
)


def get_event_batcher(
    session_factory: Callable[[], async_sessionmaker[AsyncSession]] | None = None,
) -> EventBatcher:
    """当前事件循环的进程级 batcher；`session_factory` 由装配根/测试注入。

    RunService 传入自己的模块级 `get_session_factory`（测试会 monkeypatch 它），
    不能在这里直接取 `infrastructure.db` 的全局工厂，否则测试注入的隔离库会被绕过。
    """
    loop = asyncio.get_running_loop()
    batcher = _batchers.get(loop)
    if batcher is None:
        if session_factory is None:
            from ..infrastructure.db import get_session_factory as session_factory
        assert session_factory is not None
        window_ms = float(os.environ.get("EVENT_BATCH_WINDOW_MS", "10"))
        batcher = EventBatcher(
            session_factory,
            flush_interval_sec=max(0.001, window_ms / 1000.0),
        )
        _batchers[loop] = batcher
    return batcher


async def close_event_batcher() -> None:
    """应用 shutdown：关闭当前循环的 batcher（不创建新实例），让未完成提交显式失败。"""
    loop = asyncio.get_running_loop()
    batcher = _batchers.pop(loop, None)
    if batcher is not None:
        await batcher.aclose()


def reset_event_batcher() -> None:
    """测试专用：取消并丢弃 batcher（不等待；sync fixture 里也能调用）。"""
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        batcher = _batchers.pop(loop, None)
        if batcher is not None:
            batcher.cancel()
        return
    for batcher in list(_batchers.values()):
        batcher.cancel()
    _batchers.clear()
