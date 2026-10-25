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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from muad_contracts import BotSnapshotItem
from muad_contracts.im import InboundEvent
from muad_im_gateway.application.bot_snapshot import (
    BotSnapshotCache,
)
from muad_im_gateway.channels.wecom.adapter import (
    WeComAdapter,
)
from muad_im_gateway.channels.wecom.sdk_port import (
    WeComInboundMessage,
)

from .inbound import InboundPublisher, PublishOutcome
from .lease import BotLease, RedisLeaseManager
from .metrics import record_inbound, record_lease, record_snapshot, set_owned_bots
from .outbound import OutboundRunner
from .reply_store import ReplyStore

logger = logging.getLogger(__name__)


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
        adapter: WeComAdapter,
        leases: RedisLeaseManager,
        replies: ReplyStore,
        publisher: InboundPublisher,
        outbound: OutboundRunner,
        snapshot: BotSnapshotCache,
        renew_sec: float,
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
                bot_id: state.value
                for bot_id, state in self._adapter.connection_states.items()
            },
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
        event = InboundEvent(
            event_id=uuid.uuid4(),
            channel="WECOM",
            tenant_id=self._tenant_id,
            bot_id=message.bot_id,
            external_message_id=message.message_id,
            external_conversation_id=message.external_conversation_id,
            external_user_id=message.external_user_id,
            text=message.text,
            attachment_keys=tuple(
                f"{message.message_id}#{index}" for index in range(len(message.media))
            ),
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


async def drain_inbound(
    runtime: ConnectorRuntime, messages: Iterable[WeComInboundMessage]
) -> None:
    """测试辅助：把消息逐个投递到 runtime 队列并等待发布（不改变生产路径）。"""
    for message in messages:
        runtime.raw_sink(message)
    await asyncio.sleep(0)
