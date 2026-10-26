"""Connector 运行时：快照 → 逐 bot 租约 → 连接/入站/出站生命周期。

职责链：

1. 每拍拉取 Console bot 快照（复用 Gateway 已验证的分页/不完整不发布语义）；
2. 对每个启用的目标通道 bot 尝试租约；**只有 owner** 才把条目交给 `WeComAdapter`；
3. 入站原始消息（含 reply_ref）写回复存储后发布到 `im.inbound`；
4. 出站按 bot 流消费；命令执行由 `CommandExecutor` 负责 fencing/降级；
5. 租约丢失立即从快照移除该 bot → 适配器断开连接；新 owner 在 TTL 后接管。

This module deliberately keeps channel-specific SDK/类型 out of the orchestration: the adapter
implements `ConnectorChannelPort`, and the runtime only sees bot snapshots and opaque messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from muad_contracts import AttachmentFailure, AttachmentRef, BotSnapshotItem, ChannelEnvelope
from muad_contracts.im import InboundEvent
from muad_im_core import BotSnapshotCache, InboundPublisher, PublishOutcome
from muad_im_gateway.application.attachment_gate import (
    ATTACHMENT_COUNT_EXCEEDED,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_MESSAGE,
    AttachmentCandidate,
    evaluate_gate,
)
from muad_im_gateway.application.inbound_attachments import (
    AttachmentConflictError,
    InboundAttachmentStore,
)
from muad_im_gateway.channels.base import (
    ATTACHMENT_FETCH_FAILED,
    AttachmentFetchError,
    AttachmentSource,
    FetchedAttachment,
)

from .channels.wecom.sdk_port import (
    WeComInboundMessage,
)
from .lease import BotLease, RedisLeaseManager
from .metrics import record_inbound, record_lease, record_snapshot, set_owned_bots
from .outbound import ConnectorChannelPort, OutboundRunner
from .reply_store import ReplyStore

logger = logging.getLogger(__name__)


class ConnectorAdapterPort(ConnectorChannelPort, Protocol):
    """Connector 运行时需要的适配器能力（企微长连接 / HTTP 探针同形）。

    继承出站端口（`send_*`/`has_bot`）：同一个适配器对象同时被 runtime 与 CommandExecutor 使用。
    """

    name: str

    def set_raw_sink(
        self, sink: Callable[[WeComInboundMessage], None] | None
    ) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def apply_snapshot(self, items: Sequence[BotSnapshotItem]) -> None: ...

    async def drop_bot(self, bot_id: str) -> None: ...

    @property
    def connection_states(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class OwnedBot:
    tenant_id: str
    item: BotSnapshotItem
    lease: BotLease


class ConnectorRuntime:
    def __init__(
        self,
        *,
        tenant_id: str,
        handled_channels: frozenset[str],
        adapter: ConnectorAdapterPort,
        leases: RedisLeaseManager,
        replies: ReplyStore,
        publisher: InboundPublisher,
        outbound: OutboundRunner,
        snapshot: BotSnapshotCache,
        renew_sec: float,
        attachment_store: InboundAttachmentStore | None = None,
        max_attachment_bytes: int = MAX_ATTACHMENT_BYTES,
        inbound_queue_size: int = 10_000,
    ) -> None:
        if renew_sec <= 0:
            raise ValueError("renew_sec must be positive")
        self._tenant_id = tenant_id
        self._channels = handled_channels
        self._adapter = adapter
        self._leases = leases
        self._replies = replies
        self._publisher = publisher
        self._outbound = outbound
        self._snapshot = snapshot
        self._renew_sec = renew_sec
        #: 入站附件落盘（设计 §3.2.1 ⑤）：**字节只在内持有连接的角色里取**，落共享存储后
        #: 上行只给 `AttachmentRef`（上层不接触 URL/aes_key）。
        self._attachment_store = attachment_store
        self._max_attachment_bytes = max_attachment_bytes
        self._queue: asyncio.Queue[WeComInboundMessage] = asyncio.Queue(
            maxsize=inbound_queue_size
        )
        self._owned: dict[tuple[str, str], OwnedBot] = {}
        self._items: tuple[BotSnapshotItem, ...] = ()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopped = asyncio.Event()
        self._ready = False
        # 快照回调与续租循环都会改 `_owned`：串行化，避免一个循环把另一个刚取得的 lease
        # 当成"不在 kept 里"释放掉（实测两进程都会因此反复掉线/重连）。
        self._state_lock = asyncio.Lock()
        # 连接建立可能阻塞 10s（SDK 握手超时）：应用快照必须与续租解耦，否则 lease 会在
        # 握手期间过期，两个 Connector 互相抢连接（实测 5s TTL 下必然发生）。
        self._apply_event = asyncio.Event()
        self._desired_items: tuple[BotSnapshotItem, ...] = ()
        #: 抢不到 lease 的 bot 退避到下一窗口再试：稳态下 loser 不应该每秒对每个 bot 打 Redis。
        self._retry_after: dict[tuple[str, str], float] = {}

    # ---- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        await self._adapter.start()
        swept = await self._publisher.sweep_lost()
        if swept.lost:
            record_inbound("lost", swept.lost)
            logger.error("connector_inbound_lost_on_startup lost=%s", swept.lost)
        await self._snapshot.refresh()
        self._ready = self._snapshot.is_ready()
        self._tasks = [
            asyncio.create_task(self._snapshot.run_forever(), name="connector.snapshot"),
            asyncio.create_task(self._lease_loop(), name="connector.lease"),
            asyncio.create_task(self._apply_loop(), name="connector.apply"),
            asyncio.create_task(self._inbound_loop(), name="connector.inbound"),
            asyncio.create_task(self._outbound.run_forever(), name="connector.outbound"),
        ]

    async def stop(self) -> None:
        self._stopped.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for owned in tuple(self._owned.values()):
            with contextlib.suppress(Exception):
                await self._leases.release(owned.lease)
        self._owned.clear()
        self._outbound.stop()
        set_owned_bots(0)
        await self._adapter.stop()

    # ---- readiness ------------------------------------------------------------
    @property
    def owned_bots(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._owned))

    @property
    def ready(self) -> bool:
        return self._ready and self._snapshot.is_ready()

    def readiness_detail(self) -> dict[str, object]:
        return {
            "owned_bots": [f"{tenant}:{bot}" for tenant, bot in self.owned_bots],
            "snapshot_revision": self._snapshot.revision,
            "connection_states": {
                bot_id: getattr(state, "value", state)
                for bot_id, state in self._adapter.connection_states.items()
            },
            # 未 CONNECTED 的 bot 只在**持有连接的角色**里可见（Gateway 的 readyz 不该报
            # 自己没连的 bot）。适配器没声明该能力时为空。
            "degraded_bots": dict(getattr(self._adapter, "degraded_bots", {}) or {}),
        }

    # ---- snapshot / lease -----------------------------------------------------
    async def on_snapshot(self, items: tuple[BotSnapshotItem, ...]) -> None:
        self._items = tuple(items)
        record_snapshot("ok")
        await self._reconcile()

    async def _reconcile(self) -> None:
        async with self._state_lock:
            await self._reconcile_locked()
        # 连接建立放到独立 worker：续租循环绝不能被 SDK 握手阻塞。
        self._apply_event.set()

    async def _reconcile_locked(self) -> None:
        desired = {
            (self._tenant_id, item.bot_id): item
            for item in self._items
            if item.channel in self._channels and item.enabled
        }
        kept: dict[tuple[str, str], OwnedBot] = {}
        for key, item in desired.items():
            current = self._owned.get(key)
            if current is not None:
                kept[key] = OwnedBot(
                    tenant_id=current.tenant_id, item=item, lease=current.lease
                )
                continue
            now = time.monotonic()
            if now < self._retry_after.get(key, 0.0):
                continue
            lease = await self._leases.acquire(*key)
            if lease is None:
                self._retry_after[key] = now + max(1.0, self._leases.ttl_sec / 3)
                logger.debug(
                    "connector_lease_busy tenant=%s bot=%s owner=%s",
                    key[0],
                    key[1],
                    self._leases.owner_id,
                )
                continue
            self._retry_after.pop(key, None)
            record_lease("acquired")
            logger.info(
                "connector_lease_acquired tenant=%s bot=%s owner=%s epoch=%s",
                key[0],
                key[1],
                self._leases.owner_id,
                lease.epoch,
            )
            kept[key] = OwnedBot(tenant_id=self._tenant_id, item=item, lease=lease)
        for key, owned in self._owned.items():
            if key in kept:
                continue
            released = False
            with contextlib.suppress(Exception):
                released = await self._leases.release(owned.lease)
            logger.info(
                "connector_lease_released tenant=%s bot=%s owner=%s epoch=%s released=%s",
                key[0],
                key[1],
                self._leases.owner_id,
                owned.lease.epoch,
                released,
            )
            record_lease("released")
        self._owned = kept
        self._desired_items = tuple(item.item for item in kept.values())
        self._outbound.set_bots(tuple(kept))
        set_owned_bots(len(kept))

    async def _lease_loop(self) -> None:
        while not self._stopped.is_set():
            await self._sleep(self._renew_sec)
            if self._stopped.is_set():
                return
            lost: list[tuple[str, str]] = []
            async with self._state_lock:
                for key, owned in tuple(self._owned.items()):
                    renewed = await self._leases.renew(owned.lease)
                    if renewed is None:
                        lost.append(key)
                        record_lease("lost")
                        logger.error(
                            "connector_lease_lost tenant=%s bot=%s epoch=%s",
                            key[0],
                            key[1],
                            owned.lease.epoch,
                        )
                        continue
                    self._owned[key] = OwnedBot(
                        tenant_id=owned.tenant_id, item=owned.item, lease=renewed
                    )
                    record_lease("renewed")
                for key in lost:
                    self._owned.pop(key, None)
                    self._retry_after.pop(key, None)
            # 失权立即断开，不能等下一次 apply（旧连接多活一秒就可能重复入站）。
            for _tenant_id, bot_id in lost:
                await self._adapter.drop_bot(bot_id)
            # 重试获取已过期但未归属的 bot（另一个 Connector 崩溃后的接管路径）。
            await self._reconcile()
            if lost:
                logger.warning("connector_owned_after_reconcile lost=%s", lost)

    async def _apply_loop(self) -> None:
        while not self._stopped.is_set():
            await self._apply_event.wait()
            if self._stopped.is_set():
                return
            self._apply_event.clear()
            items = self._desired_items
            try:
                await self._adapter.apply_snapshot(items)
            except Exception as exc:  # noqa: BLE001 - 连接失败由适配器退避，下一拍重试
                logger.warning(
                    "connector_apply_snapshot_failed error=%s: %s",
                    type(exc).__name__,
                    exc,
                )

    # ---- inbound --------------------------------------------------------------
    async def _inbound_loop(self) -> None:
        while not self._stopped.is_set():
            message = await self._queue.get()
            await self._publish_inbound(message)

    async def _publish_inbound(self, message: WeComInboundMessage) -> None:
        owned = self._owned.get((self._tenant_id, message.bot_id))
        epoch = owned.lease.epoch if owned is not None else 0
        await self._replies.remember_message(
            tenant_id=self._tenant_id,
            bot_id=message.bot_id,
            message_id=message.message_id,
            reply_ref=message.reply_id or None,
            epoch=epoch,
        )
        await self._replies.remember_route(
            tenant_id=self._tenant_id,
            bot_id=message.bot_id,
            external_user_id=message.external_user_id,
            external_conversation_id=message.external_conversation_id,
            reply_ref=message.reply_id or None,
            epoch=epoch,
        )
        attachments, failures = await self._materialize_attachments(message)
        event = InboundEvent(
            event_id=uuid.uuid4(),
            channel="WECOM",
            tenant_id=self._tenant_id,
            bot_id=message.bot_id,
            external_message_id=message.message_id,
            external_conversation_id=message.external_conversation_id,
            external_user_id=message.external_user_id,
            text=message.text,
            attachments=attachments,
            attachment_keys=tuple(failure.key for failure in failures),
            attachment_failures=failures,
            unsupported_media=message.unsupported_media,
            reply_ref=message.reply_id or None,
            received_at=datetime.now(UTC),
        )
        while not self._stopped.is_set():
            try:
                outcome = await self._publisher.publish(event)
            except Exception as exc:  # noqa: BLE001 - Redis 抖动不能吞消息
                record_inbound("failed")
                logger.error(
                    "connector_inbound_publish_failed message_id=%s error=%s",
                    message.message_id,
                    type(exc).__name__,
                )
                await self._sleep(1.0)
                continue
            record_inbound(
                "published" if outcome is PublishOutcome.PUBLISHED else "duplicate"
            )
            return

    async def _materialize_attachments(
        self, message: WeComInboundMessage
    ) -> tuple[tuple[AttachmentRef, ...], tuple[AttachmentFailure, ...]]:
        """取件 + 门控 + 落共享存储；失败逐项翻译成渠道中立原因码（不静默丢）。

        与 Gateway 原先的本地取件完全同序：数量上限先行（超出的不下载）→ 下载/解密 →
        类型/体积门控 → 原子落盘。区别只是执行者换成了持有长连接的 Connector。
        """
        store = self._attachment_store
        if store is None or not message.media:
            return (), ()
        adapter = self._adapter
        if not isinstance(adapter, AttachmentSource):
            return (), tuple(
                AttachmentFailure(key=f"{message.message_id}#{index}",
                                  code=ATTACHMENT_FETCH_FAILED)
                for index in range(len(message.media))
            )
        envelope = _media_envelope(adapter.name, message)
        refs: list[AttachmentRef] = []
        failures: list[AttachmentFailure] = []
        fetched: list[tuple[int, FetchedAttachment]] = []
        limit = min(len(message.media), MAX_ATTACHMENTS_PER_MESSAGE)
        for index in range(len(message.media)):
            key = f"{message.message_id}#{index}"
            if index >= limit:
                failures.append(AttachmentFailure(key=key, code=ATTACHMENT_COUNT_EXCEEDED))
                continue
            try:
                content = await adapter.fetch_attachment(
                    envelope, index, max_bytes=self._max_attachment_bytes
                )
            except AttachmentFetchError as exc:
                logger.warning(
                    "connector_attachment_fetch_failed message_id=%s index=%s code=%s",
                    message.message_id,
                    index,
                    exc.code,
                )
                failures.append(AttachmentFailure(key=key, code=exc.code))
                continue
            fetched.append((index, content))
        decision = evaluate_gate(
            [
                AttachmentCandidate(
                    media_type=content.media_type,
                    size=len(content.data),
                    filename=content.filename,
                )
                for _index, content in fetched
            ]
        )
        rejected = {rejection.index: rejection.code for rejection in decision.rejected}
        for position, (index, content) in enumerate(fetched):
            key = f"{message.message_id}#{index}"
            code = rejected.get(position)
            if code is not None:
                failures.append(AttachmentFailure(key=key, code=code))
                continue
            try:
                refs.append(
                    store.persist(
                        token=message.message_id,
                        index=index,
                        content=content,
                        source_channel=adapter.name,
                    )
                )
            except (AttachmentConflictError, OSError) as exc:
                logger.error(
                    "connector_attachment_persist_failed message_id=%s index=%s error=%s",
                    message.message_id,
                    index,
                    type(exc).__name__,
                )
                failures.append(AttachmentFailure(key=key, code=ATTACHMENT_FETCH_FAILED))
        return tuple(refs), tuple(failures)

    # ---- helpers --------------------------------------------------------------
    def raw_sink(self, message: WeComInboundMessage) -> None:
        """适配器回调 → 有界队列；队满时显式失败（不静默丢）。"""
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.critical(
                "connector_inbound_queue_full bot_id=%s message_id=%s",
                message.bot_id,
                message.message_id,
            )
            record_inbound("failed")

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stopped.wait(), timeout=seconds)


def _media_envelope(channel: str, message: WeComInboundMessage) -> ChannelEnvelope:
    """取件只需要渠道身份与消息 id（媒体引用按 message_id 存在适配器里）。"""
    return ChannelEnvelope(
        channel=channel,
        bot_id=message.bot_id,
        external_user_id=message.external_user_id,
        external_conversation_id=message.external_conversation_id,
        message_id=message.message_id,
        text="",
    )


async def drain_inbound(
    runtime: ConnectorRuntime, messages: Iterable[WeComInboundMessage]
) -> None:
    """测试辅助：把消息逐个投递到 runtime 队列并等待发布（不改变生产路径）。"""
    for message in messages:
        runtime.raw_sink(message)
    await asyncio.sleep(0)
