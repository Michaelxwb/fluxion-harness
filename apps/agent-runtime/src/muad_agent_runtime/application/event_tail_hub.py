"""共享事件追尾、提交后唤醒与有界补查（ADR-02；design §3.2 / §3.6；TASK-060）。

设计要点（每条都有对应验收断言）：

- **同会话共享上游追尾**：同进程内同一 `(tenant, conversation)` 的多个订阅者共享同一轮上游
  读取（水位 + 分页），观察者数量不线性放大查询（ADR-02「减少随观察者数量线性重复的查询」）；
  订阅者各自持有游标，从共享窗口按 `seq > 游标` 取行。
- **注册唤醒→读水位→按页追到水位→等待或批量补查**（design §3.2）：订阅者**先注册唤醒再读
  水位**，提交后的唤醒不会掉进"读取之后、等待之前"的缝隙；进程级补查任务每
  `run_event_fallback_poll_sec` 用**一条批量水位查询**覆盖全部活跃会话，唤醒丢失只退化为
  低频补查，不丢事件，也不随会话数线性增加查询。
- **提交后唤醒**：写入方在**事务提交之后**调用 `publish`（轻量提示，不携带事实）；Redis
  Pub/Sub 只作跨进程通知，连不上即降级为 PG 补查（`degraded` 可见，绝不抛回写入路径）。
- **补查**：断线重连按水位补拉缺失区间；共享窗口淘汰（慢消费者）后按持久水位分页重建，
  每次重建计数可见（`lagged_rebuilds`）。
- **退出事务才 yield**：所有行在读取会话内物化为 `CanonicalEventView`，会话关闭后才交给
  订阅者（慢 SSE 不占数据库连接；design §3.6）。
- **去重与合法跳号**：`seq <= 游标` 一律不再产出（重复 seq 不重复投递）；内部事件被过滤造成
  的 canonical 跳号是合法事实，不判丢包、不触发补拉循环。
- **水位与游标分型**：`watermark` 是真实持久水位（会话行 `last_seq` 与已持久行的最大 seq
  取大者）；`checkpoint` 只回真实水位，**不另分配 canonical seq**；`check_cursor` 对超前游标
  （`CURSOR_AHEAD`）与已过期游标（`CURSOR_EXPIRED`，最早可用行已被保留策略淘汰）分别显式失败。
- **心跳不推进业务游标**：本模块不产出心跳帧（由 HTTP 层产出）；等待轮次不移动游标，
  游标只在真正产出已提交行时前进。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol, cast

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common.db_counters import db_stage_scope
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models.runtime import CanonicalEvent, Conversation
from ..metrics import (
    EVENT_STREAM_HINT_METRIC,
    record_outcome,
    record_tail_outcome,
)

logger = logging.getLogger(__name__)

#: 跨进程轻量唤醒通道（Redis Pub/Sub 只通知，不承载事实；见 ADR-02）。
WAKEUP_CHANNEL = "runtime:events"
#: 单轮上游读取的页大小：水位内分页追平，不一次读全量（有界内存）。
PAGE_ROWS = 200
#: 共享窗口行数上限：超出即从窗口前部淘汰，落后订阅者按持久水位补查重建。
WINDOW_ROWS = 512
#: hint 不可达/断开后的重连退避；期间只按 PG 补查（降级可见）。
HINT_RETRY_SEC = 30.0


class HintUnavailable(RuntimeError):
    """唤醒提示不可用（Redis 连不上/未就绪）：只影响唤醒时延，不影响事实。"""


class WakeupHint(Protocol):
    """提交后唤醒的跨进程提示端口：只通知，不携带事实，失败即降级为 PG 补查。"""

    enabled: bool
    degraded: bool

    async def publish(self, payload: str) -> None: ...

    def listen(self) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class NullWakeupHint:
    """无 Redis 装配：只在本进程内唤醒，跨进程靠 PG 有界补查（不伪装成已送达）。"""

    enabled = False
    degraded = False

    async def publish(self, payload: str) -> None:
        return None

    async def listen(self) -> AsyncIterator[str]:
        return
        yield  # pragma: no cover —— 空异步生成器（协议形状）

    async def aclose(self) -> None:
        return None


class RedisHintClient(Protocol):
    async def ping(self) -> bool: ...

    async def publish(self, channel: str, message: str) -> int: ...

    def pubsub(self) -> RedisHintPubSub: ...

    async def aclose(self) -> None: ...


class RedisHintPubSub(Protocol):
    async def subscribe(self, *channels: str) -> Any: ...

    def listen(self) -> AsyncIterator[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


def _build_redis_client(redis_url: str, max_connections: int) -> RedisHintClient:
    return cast(
        RedisHintClient,
        Redis.from_url(redis_url, decode_responses=True, max_connections=max_connections),
    )


class RedisWakeupHint:
    """真实 Redis Pub/Sub 提示：连不上/断开即显式降级（`degraded`），退避后重试。"""

    def __init__(
        self,
        redis_url: str,
        *,
        max_connections: int = 50,
        client_factory: Callable[[str, int], RedisHintClient] = _build_redis_client,
    ) -> None:
        self.enabled = True
        self.degraded = False
        self._redis_url = redis_url
        self._max_connections = max_connections
        self._client_factory = client_factory
        self._client: RedisHintClient | None = None
        self._retry_at = 0.0

    async def _connected(self) -> RedisHintClient | None:
        if self._client is not None:
            return self._client
        now = time.monotonic()
        if self.degraded and now < self._retry_at:
            return None
        client = self._client_factory(self._redis_url, self._max_connections)
        was_degraded = self.degraded
        try:
            await client.ping()
        except RedisError:
            self.degraded = True
            record_outcome(EVENT_STREAM_HINT_METRIC, "degraded")
            self._retry_at = now + HINT_RETRY_SEC
            logger.warning("event_tail_hint_unavailable", extra={"retry_in_sec": HINT_RETRY_SEC})
            with suppress(Exception):
                await client.aclose()
            return None
        self.degraded = False
        self._client = client
        if was_degraded:
            # 降级恢复必须可见：Redis 只影响唤醒时延，恢复后不该继续背着"降级中"。
            record_outcome(EVENT_STREAM_HINT_METRIC, "ok")
        return client

    async def publish(self, payload: str) -> None:
        client = await self._connected()
        if client is None:
            raise HintUnavailable("wakeup hint unavailable")
        try:
            await client.publish(WAKEUP_CHANNEL, payload)
        except RedisError as exc:
            self._mark_degraded("event_tail_hint_publish_failed")
            raise HintUnavailable("wakeup hint publish failed") from exc

    async def listen(self) -> AsyncIterator[str]:
        client = await self._connected()
        if client is None:
            raise HintUnavailable("wakeup hint unavailable")
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(WAKEUP_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, str):
                    yield data
        except RedisError as exc:
            self._mark_degraded("event_tail_hint_listen_failed")
            raise HintUnavailable("wakeup hint listen failed") from exc
        finally:
            with suppress(Exception):
                await pubsub.aclose()

    def _mark_degraded(self, event: str) -> None:
        self.degraded = True
        record_outcome(EVENT_STREAM_HINT_METRIC, "degraded")
        self._retry_at = time.monotonic() + HINT_RETRY_SEC
        logger.warning(event, extra={"retry_in_sec": HINT_RETRY_SEC})
        client, self._client = self._client, None
        if client is not None:
            asyncio.get_running_loop().create_task(_close_quietly(client))

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await _close_quietly(client)


async def _close_quietly(client: RedisHintClient) -> None:
    with suppress(Exception):
        await client.aclose()


def build_wakeup_hint(redis_url: str | None, *, max_connections: int = 50) -> WakeupHint:
    """装配根注入：无 `REDIS_URL` 即进程内唤醒（跨进程只靠 PG 有界补查）。"""
    if not redis_url:
        return NullWakeupHint()
    return RedisWakeupHint(redis_url, max_connections=max_connections)


@dataclass(frozen=True, slots=True)
class CanonicalEventView:
    """已提交 canonical 行的事实形状；满足 `public_events.CanonicalRow` 投影协议。"""

    id: uuid.UUID
    tenant_id: str
    conversation_id: uuid.UUID
    run_id: uuid.UUID | None
    seq: int
    event_type: str
    stream_type: str | None
    payload_json: dict[str, Any]
    artifact_id: uuid.UUID | None
    create_time: datetime


def _view(row: CanonicalEvent) -> CanonicalEventView:
    return CanonicalEventView(
        id=row.id,
        tenant_id=row.tenant_id,
        conversation_id=row.conversation_id,
        run_id=row.run_id,
        seq=int(row.seq),
        event_type=row.event_type,
        stream_type=row.stream_type,
        payload_json=dict(row.payload_json or {}),
        artifact_id=row.artifact_id,
        create_time=row.create_time,
    )


@dataclass(frozen=True, slots=True)
class TailBatch:
    """一轮追尾产出：已提交行 + 本轮真实持久水位 + 是否追平（追平时水位可作安全 checkpoint）。"""

    rows: tuple[CanonicalEventView, ...]
    through_seq: int
    caught_up: bool
    lagged: bool = False


@dataclass(slots=True)
class TailHubStats:
    """可观测结果（进程内快照）：共享读取、唤醒、补查与降级都留痕，不静默。"""

    upstream_rounds: int = 0
    pages_read: int = 0
    wakeups: int = 0
    #: 进程级批量补查的**轮次**（不是每会话次数；一次查询覆盖当前全部活跃会话）。
    fallback_polls: int = 0
    lagged_rebuilds: int = 0
    hint_failures: int = 0
    cursor_ahead: int = 0
    cursor_expired: int = 0
    active_channels: int = 0
    hint_degraded: bool = False


@dataclass(slots=True)
class _Subscriber:
    cursor: int
    waiter: asyncio.Event


@dataclass(slots=True)
class _Channel:
    """一个会话的共享追尾状态：共享窗口 + 上游读位 + 订阅者集合。"""

    tenant_id: str
    conversation_id: uuid.UUID
    rows: deque[CanonicalEventView] = field(default_factory=deque)
    floor_seq: int = 0
    read_seq: int = 0
    through_seq: int = 0
    idle: bool = True
    subscribers: list[_Subscriber] = field(default_factory=list)
    inflight: asyncio.Task[None] | None = None


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    """进程内真实会话工厂（延迟导入：测试与重装配会重建 engine 缓存）。"""
    from ..infrastructure.db import get_session_factory

    return get_session_factory()


def _consume_task_exception(task: asyncio.Task[None]) -> None:
    """共享读取任务的异常由 await 方负责上抛；无等待者时在此取回，不留未处理告警。"""
    if not task.cancelled():
        task.exception()


def _hint_payload(tenant_id: str, conversation_id: uuid.UUID) -> str:
    return f"{tenant_id}|{conversation_id}"


def _parse_hint(payload: str) -> tuple[str, uuid.UUID] | None:
    tenant_id, _, raw = payload.partition("|")
    if not tenant_id or not raw:
        return None
    try:
        return tenant_id, uuid.UUID(raw)
    except ValueError:
        return None


class EventTailHub:
    """进程内共享追尾协调器：按会话共享上游读取，提交后唤醒，超时按 PG 有界补查。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], async_sessionmaker[AsyncSession]] | None = None,
        fallback_poll_sec: float = 5.0,
        page_size: int = PAGE_ROWS,
        window_rows: int = WINDOW_ROWS,
        hint: WakeupHint | None = None,
    ) -> None:
        if fallback_poll_sec <= 0:
            raise ValueError("fallback poll interval must be positive")
        if page_size < 1 or window_rows < page_size:
            raise ValueError("shared window must hold at least one page")
        self._session_factory = session_factory or _default_session_factory
        self._fallback_poll_sec = fallback_poll_sec
        self._page_size = page_size
        self._window_rows = window_rows
        self._hint: WakeupHint = hint or NullWakeupHint()
        self._channels: dict[tuple[str, uuid.UUID], _Channel] = {}
        self._stats = TailHubStats()
        self._listener: asyncio.Task[None] | None = None
        #: 进程级补查调度：一次批量水位查询覆盖所有活跃会话，避免每流一个超时查询。
        self._scheduler: asyncio.Task[None] | None = None

    def bind_session_factory(
        self, session_factory: Callable[[], async_sessionmaker[AsyncSession]]
    ) -> None:
        """装配根注入：绑定本进程的真实会话工厂（测试与重装配可再次绑定）。"""
        self._session_factory = session_factory

    # ---------------------------------------------------------------- 提交后唤醒

    async def publish(self, *, tenant_id: str, conversation_id: uuid.UUID) -> None:
        """提交后唤醒（调用点必须在事务提交之后）：先本地唤醒，再尽力发跨进程提示。

        提示是可选加速：失败只计数并降级为 PG 有界补查（`hint_failures`/`hint_degraded`），
        绝不把失败抛回写入路径，也不改写任何事实。
        """
        channel = self._channels.get((tenant_id, conversation_id))
        if channel is not None:
            self._wake(channel)
        if not self._hint.enabled:
            return
        try:
            await self._hint.publish(_hint_payload(tenant_id, conversation_id))
        except Exception:  # noqa: BLE001 —— hint 只影响唤醒时延，不影响事实与结果
            self._stats.hint_failures += 1
            record_tail_outcome("hint_failure")
            logger.warning(
                "event_tail_hint_publish_failed", extra={"conversation_id": str(conversation_id)}
            )

    def _wake(self, channel: _Channel) -> None:
        self._stats.wakeups += 1
        record_tail_outcome("wakeup")
        for subscriber in channel.subscribers:
            subscriber.waiter.set()

    # ------------------------------------------------------------------- 追尾

    async def tail(
        self, *, tenant_id: str, conversation_id: uuid.UUID, after_seq: int = 0
    ) -> AsyncIterator[TailBatch]:
        """共享追尾一个会话的 canonical 流；退出读取事务后才向调用方 yield。"""
        channel = self._channel(tenant_id, conversation_id)
        subscriber = _Subscriber(cursor=max(0, after_seq), waiter=asyncio.Event())
        channel.subscribers.append(subscriber)
        self._ensure_listener()
        self._ensure_scheduler()
        try:
            async for batch in self._follow(channel, subscriber):
                yield batch
        finally:
            channel.subscribers.remove(subscriber)
            if not channel.subscribers:
                self._channels.pop((tenant_id, conversation_id), None)

    async def _follow(self, channel: _Channel, subscriber: _Subscriber) -> AsyncIterator[TailBatch]:
        """注册唤醒 → 读水位 → 按页追到水位 → 等待（补查由进程级批量任务负责）。

        等待超时只产出一个**空 caught_up 批**给调用方评估终态，不再触发每会话 DB 查询；
        下一次真正的水位变化由 `_poll_channels` 批量发现后 set waiter。
        """
        first_round = True
        skip_read = False
        while True:
            # ① 先注册：读取期间到达的唤醒留在 waiter 上，不会掉进读取/等待之间的缝隙。
            subscriber.waiter.clear()
            if not skip_read:
                await self._shared_read(channel)  # ② 读水位 + 至多一页（同会话共享）
                if first_round:
                    self._assert_cursor_in_range(channel, subscriber.cursor)
                    first_round = False
            skip_read = False
            async for batch in self._drain(channel, subscriber):
                yield batch
            if channel.read_seq < channel.through_seq or subscriber.cursor < channel.read_seq:
                continue  # 上游还有未读页，或本订阅者还落后于已读入的行：立刻继续追（不等待）
            if not await self._wait(subscriber):
                # 补查节拍到点但没有新水位：给调用方一个空批评估终态，绝不在这里查库。
                yield TailBatch(
                    rows=(),
                    through_seq=channel.through_seq,
                    caught_up=True,
                )
                skip_read = True

    def _assert_cursor_in_range(self, channel: _Channel, cursor: int) -> None:
        if cursor > channel.through_seq:
            self._stats.cursor_ahead += 1
            raise AppError(
                ErrorCode.CURSOR_AHEAD,
                data={"after_seq": cursor, "through_seq": channel.through_seq},
            )

    async def _shared_read(self, channel: _Channel) -> None:
        """同一会话同一轮只做一次上游读取；并发订阅者共享同一次结果（single-flight）。"""
        task = channel.inflight
        if task is None or task.done():
            task = asyncio.create_task(
                cast(Coroutine[Any, Any, None], self._read_round(channel))
            )
            task.add_done_callback(_consume_task_exception)
            channel.inflight = task
        await asyncio.shield(task)

    @db_stage_scope("bg:tail")
    async def _read_round(self, channel: _Channel) -> None:
        """一轮共享上游读取：读水位；水位内读一页入共享窗口；追平且无行即空闲轮。"""
        self._stats.upstream_rounds += 1
        page: list[CanonicalEventView] = []
        async with self._session_factory()() as session:
            if channel.read_seq >= channel.through_seq:
                channel.through_seq = max(
                    channel.through_seq,
                    await self._watermark_in(
                        session,
                        tenant_id=channel.tenant_id,
                        conversation_id=channel.conversation_id,
                    ),
                )
            if channel.read_seq < channel.through_seq:
                page = await self._page_in(session, channel, after_seq=channel.read_seq)
                self._stats.pages_read += 1
        channel.idle = not page
        self._append_window(channel, page)

    def _append_window(self, channel: _Channel, page: list[CanonicalEventView]) -> None:
        channel.rows.extend(page)
        if page:
            channel.read_seq = page[-1].seq
        while len(channel.rows) > self._window_rows:
            channel.floor_seq = channel.rows.popleft().seq

    async def _drain(self, channel: _Channel, subscriber: _Subscriber) -> AsyncIterator[TailBatch]:
        if subscriber.cursor < channel.floor_seq:
            async for batch in self._rebuild(channel, subscriber):
                yield batch
        rows = tuple(row for row in channel.rows if row.seq > subscriber.cursor)
        if rows:
            subscriber.cursor = rows[-1].seq
            yield TailBatch(
                rows=rows,
                through_seq=channel.through_seq,
                caught_up=subscriber.cursor >= channel.through_seq,
            )
            return
        if channel.idle and subscriber.cursor >= channel.through_seq:
            # 追平水位且本轮无新行：交给调用方判定终态/等待（心跳不经过这里，不推进游标）。
            yield TailBatch(rows=(), through_seq=channel.through_seq, caught_up=True)

    async def _rebuild(self, channel: _Channel, subscriber: _Subscriber) -> AsyncIterator[TailBatch]:
        """慢消费者：共享窗口已淘汰其游标之前的行，按持久水位分页补查重建（计数可见）。"""
        self._stats.lagged_rebuilds += 1
        record_tail_outcome("lagged_rebuild")
        logger.warning(
            "event_tail_lagged_rebuild",
            extra={
                "conversation_id": str(channel.conversation_id),
                "cursor": subscriber.cursor,
                "floor_seq": channel.floor_seq,
            },
        )
        target = channel.floor_seq
        while subscriber.cursor < target:
            async with self._session_factory()() as session:
                page = await self._page_in(session, channel, after_seq=subscriber.cursor)
                self._stats.pages_read += 1
            if not page:
                # 缺失区间的行已不在（保留策略淘汰）：游标推进到窗口地板，绝不空转重查。
                subscriber.cursor = target
                return
            subscriber.cursor = page[-1].seq
            yield TailBatch(
                rows=tuple(page),
                through_seq=channel.through_seq,
                caught_up=subscriber.cursor >= channel.through_seq,
                lagged=True,
            )

    async def _wait(self, subscriber: _Subscriber) -> bool:
        """等待唤醒；超时返回 False（补查节拍只产出空批，不查库）。"""
        try:
            await asyncio.wait_for(subscriber.waiter.wait(), timeout=self._fallback_poll_sec)
            return True
        except TimeoutError:
            return False

    def _ensure_scheduler(self) -> None:
        """启动进程级补查任务：没有活跃订阅时自然退出，下一条流再启动。"""
        if self._scheduler is not None and not self._scheduler.done():
            return
        self._scheduler = asyncio.create_task(self._poll_loop())
        self._scheduler.add_done_callback(_consume_task_exception)

    async def _poll_loop(self) -> None:
        """进程级补查：一次批量水位查询覆盖所有活跃会话，逐会话唤醒订阅者。"""
        while self._channels:
            await asyncio.sleep(self._fallback_poll_sec)
            try:
                await self._poll_channels()
                record_tail_outcome("fallback_poll")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 补查只是恢复路径，失败要可见但不杀订阅者
                self._stats.hint_failures += 1
                record_tail_outcome("fallback_failure")
                logger.warning("event_tail_fallback_poll_failed")

    @db_stage_scope("bg:tail")
    async def _poll_channels(self) -> None:
        """批量补查水位：按租户一次 `IN (...)` 查询，不为每个会话各发一条语句。"""
        channels = [channel for channel in self._channels.values() if channel.subscribers]
        if not channels:
            return
        by_tenant: dict[str, list[_Channel]] = {}
        for channel in channels:
            by_tenant.setdefault(channel.tenant_id, []).append(channel)
        for tenant_id, group in by_tenant.items():
            self._stats.fallback_polls += 1
            by_id = {channel.conversation_id: channel for channel in group}
            async with self._session_factory()() as session:
                rows = (
                    await session.execute(
                        sa.select(Conversation.id, Conversation.last_seq).where(
                            Conversation.tenant_id == tenant_id,
                            Conversation.id.in_(tuple(by_id)),
                            Conversation.is_deleted.is_(False),
                        )
                    )
                ).all()
            for conversation_id, last_seq in rows:
                target = by_id.get(conversation_id)
                if target is None:
                    continue
                if int(last_seq) > target.through_seq:
                    target.through_seq = int(last_seq)
                    self._wake(target)

    def _channel(self, tenant_id: str, conversation_id: uuid.UUID) -> _Channel:
        key = (tenant_id, conversation_id)
        channel = self._channels.get(key)
        if channel is None:
            channel = _Channel(tenant_id=tenant_id, conversation_id=conversation_id)
            self._channels[key] = channel
        return channel

    # --------------------------------------------------------------- 水位与游标

    async def watermark(self, *, tenant_id: str, conversation_id: uuid.UUID) -> int:
        async with self._session_factory()() as session:
            return await self._watermark_in(
                session, tenant_id=tenant_id, conversation_id=conversation_id
            )

    async def earliest_available(self, *, tenant_id: str, conversation_id: uuid.UUID) -> int | None:
        """最早可用行 seq（保留策略淘汰后前移）；无行返回 None。"""
        async with self._session_factory()() as session:
            value = await session.scalar(
                sa.select(sa.func.min(CanonicalEvent.seq)).where(
                    CanonicalEvent.tenant_id == tenant_id,
                    CanonicalEvent.conversation_id == conversation_id,
                    CanonicalEvent.is_deleted.is_(False),
                )
            )
        return int(value) if value is not None else None

    async def check_cursor(
        self, *, tenant_id: str, conversation_id: uuid.UUID, after_seq: int
    ) -> int:
        """游标分型：超前（`CURSOR_AHEAD`）与已过期（`CURSOR_EXPIRED`）显式失败，返回真实水位。

        保留期内的普通跳号（内部事件被过滤）不是过期：过期只看最早可用行是否已被淘汰。
        """
        through = await self.watermark(tenant_id=tenant_id, conversation_id=conversation_id)
        if after_seq > through:
            self._stats.cursor_ahead += 1
            record_tail_outcome("cursor_ahead")
            raise AppError(
                ErrorCode.CURSOR_AHEAD, data={"after_seq": after_seq, "through_seq": through}
            )
        floor = await self.earliest_available(
            tenant_id=tenant_id, conversation_id=conversation_id
        )
        if floor is not None and after_seq + 1 < floor:
            self._stats.cursor_expired += 1
            record_tail_outcome("cursor_expired")
            raise AppError(
                ErrorCode.CURSOR_EXPIRED, data={"after_seq": after_seq, "earliest_seq": floor}
            )
        return through

    async def checkpoint(self, *, tenant_id: str, conversation_id: uuid.UUID) -> int:
        """安全 checkpoint：只回真实持久水位，不另分配 canonical seq（design §3.6）。"""
        return await self.watermark(tenant_id=tenant_id, conversation_id=conversation_id)

    # ------------------------------------------------------------------- 读取

    async def _watermark_in(
        self, session: AsyncSession, *, tenant_id: str, conversation_id: uuid.UUID
    ) -> int:
        """真实持久水位：会话行 `last_seq` 与已持久行最大 seq 取大者。

        `last_seq` 由会话行锁单调分配（分配顺序即提交顺序）；取已持久行的最大值让水位对
        "直接写入的行"同样成立，绝不把水位报得比真实已提交事实更小。
        """
        row = (
            await session.execute(
                sa.select(
                    Conversation.last_seq,
                    sa.select(sa.func.max(CanonicalEvent.seq))
                    .where(
                        CanonicalEvent.tenant_id == Conversation.tenant_id,
                        CanonicalEvent.conversation_id == Conversation.id,
                        CanonicalEvent.is_deleted.is_(False),
                    )
                    .scalar_subquery()
                    .label("persisted_seq"),
                ).where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                    Conversation.is_deleted.is_(False),
                )
            )
        ).first()
        if row is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return max(int(row.last_seq), int(row.persisted_seq or 0))

    async def _page_in(
        self, session: AsyncSession, channel: _Channel, *, after_seq: int
    ) -> list[CanonicalEventView]:
        """按 seq 升序读一页已提交行：退出会话后才由调用方 yield（不占数据库连接）。"""
        rows = await session.execute(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.tenant_id == channel.tenant_id,
                CanonicalEvent.conversation_id == channel.conversation_id,
                CanonicalEvent.seq > after_seq,
                CanonicalEvent.is_deleted.is_(False),
            )
            .order_by(CanonicalEvent.seq)
            .limit(self._page_size)
        )
        return [_view(row) for row in rows.scalars().all()]

    # ------------------------------------------------------------------- 监听

    def _ensure_listener(self) -> None:
        if self._listener is not None or not self._hint.enabled:
            return
        self._listener = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        """跨进程唤醒监听：断开即退避重连；只唤醒本进程确实在追的会话。"""
        while True:
            try:
                async for payload in self._hint.listen():
                    key = _parse_hint(payload)
                    channel = self._channels.get(key) if key is not None else None
                    if channel is not None:
                        self._wake(channel)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— hint 只影响时延，退避后重试
                self._stats.hint_failures += 1
                logger.warning("event_tail_hint_listen_failed", extra={"retry_in_sec": HINT_RETRY_SEC})
                await asyncio.sleep(HINT_RETRY_SEC)

    def stats(self) -> TailHubStats:
        """可观测快照：共享读取/唤醒/补查/降级计数 + 活跃会话数。"""
        return replace(
            self._stats,
            active_channels=len(self._channels),
            hint_degraded=self._hint.degraded,
        )

    async def aclose(self) -> None:
        if self._scheduler is not None:
            scheduler, self._scheduler = self._scheduler, None
            scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler
        if self._listener is not None:
            listener, self._listener = self._listener, None
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
        await self._hint.aclose()


_hub: EventTailHub | None = None


def build_event_tail_hub(
    settings: Any = None,
    *,
    session_factory: Callable[[], async_sessionmaker[AsyncSession]] | None = None,
) -> EventTailHub:
    """装配根注入：补查周期与唤醒通道只来自启动设置（design §3.7 的唯一来源）。"""
    if settings is None:
        from muad_common import process_settings

        settings = process_settings()
    return EventTailHub(
        session_factory=session_factory,
        fallback_poll_sec=float(settings.run_event_fallback_poll_sec),
        hint=build_wakeup_hint(
            settings.redis_url, max_connections=int(settings.redis_max_connections)
        ),
    )


def get_event_tail_hub(
    session_factory: Callable[[], async_sessionmaker[AsyncSession]] | None = None,
) -> EventTailHub:
    """进程级单例：写入方与追尾方共用同一份唤醒状态（ADR-02「同进程按会话共享追尾」）。

    传入 `session_factory` 即把它绑定为该进程的真实会话工厂（测试与重装配可再次绑定，
    保证单例始终读到当前 engine 缓存而不是某个旧实例）。
    """
    global _hub
    if _hub is None:
        _hub = build_event_tail_hub(session_factory=session_factory)
    elif session_factory is not None:
        _hub.bind_session_factory(session_factory)
    return _hub


def reset_event_tail_hub() -> None:
    """测试与重装配用：丢弃进程单例（不负责关闭；调用方自行 `aclose`）。"""
    global _hub
    _hub = None
