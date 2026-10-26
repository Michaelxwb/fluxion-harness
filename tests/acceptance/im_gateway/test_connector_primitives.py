"""TASK-007 基元验收：真实 Redis 上的 lease / reply store / inbound / outbound。

真实边界：
- 真实 Redis（本会话独立 DB）执行 Lua 与 Stream；不是进程内字典替身；
- 真实 `InboundPublisher`/`CommandExecutor`/`OutboundRunner` 代码路径；
- 渠道侧只替换 SDK 客户端（`RecordingChannel`），Redis/Stream/lease 全是真的。

S-05 的**双进程**接管在 `test_connector_lease.py`；本文件先把原子语义与失败分支钉死。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from muad_common import SharedSettings
from muad_contracts import AttachmentRef, DeliveryMessage, DeliveryRouteInput
from muad_contracts.im import DeliveryCommand, DeliveryKind, InboundEvent
from muad_im_connector.lease import RedisLeaseManager
from muad_im_connector.outbound import (
    CommandExecutor,
    CommandStatus,
    OutboundRunner,
    route_of,
)
from muad_im_connector.reply_store import ReplyStore
from muad_im_core import InboundPublisher, PublishOutcome
from redis.asyncio import Redis

pytestmark = pytest.mark.integration

TENANT = "e2e-connector"
BOT = "e2e-connector-bot"


@pytest.fixture
async def redis() -> Redis:
    url = SharedSettings().redis_url or os.environ.get("REDIS_URL")
    assert url, "REDIS_URL 必须存在（acceptance conftest 提供隔离 DB）"
    client = Redis.from_url(url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


def _event(message_id: str, text: str = "hi") -> InboundEvent:
    return InboundEvent(
        event_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id=TENANT,
        bot_id=BOT,
        external_message_id=message_id,
        external_user_id="user-1",
        external_conversation_id="chat-1",
        text=text,
        received_at=datetime.now(UTC),
        reply_ref=f"req-{message_id}",
    )


def _command(kind: DeliveryKind, **overrides: object) -> DeliveryCommand:
    values: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "channel": "WECOM",
        "tenant_id": TENANT,
        "bot_id": BOT,
        "run_id": uuid.uuid4(),
        "external_user_id": "user-1",
        "external_conversation_id": "chat-1",
        "kind": kind,
        "text": "hello",
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return DeliveryCommand(**values)  # type: ignore[arg-type]


async def _lease(
    redis: Redis, owner: str = "owner-a", ttl: float = 5.0, epoch: int | None = None
) -> tuple[RedisLeaseManager, object]:
    await redis.delete(
        f"connector:bot:{TENANT}:{BOT}", f"connector:bot:{TENANT}:{BOT}:epoch"
    )
    if epoch is not None:
        await redis.set(f"connector:bot:{TENANT}:{BOT}:epoch", epoch)
    manager = RedisLeaseManager(redis, owner_id=owner, ttl_sec=ttl)
    lease = await manager.acquire(TENANT, BOT)
    assert lease is not None
    return manager, lease


async def test_lease_has_single_owner_and_monotonic_epoch(redis: Redis) -> None:
    a = RedisLeaseManager(redis, owner_id="owner-a", ttl_sec=5.0)
    b = RedisLeaseManager(redis, owner_id="owner-b", ttl_sec=5.0)
    lease_a = await a.acquire(TENANT, BOT)
    assert lease_a is not None
    assert await b.acquire(TENANT, BOT) is None, "同一 bot 不得出现第二个 owner"
    assert await a.renew(lease_a) is not None
    assert await b.renew(lease_a) is None, "别人的 lease 不能续"
    await a.release(lease_a)
    lease_b = await b.acquire(TENANT, BOT)
    assert lease_b is not None
    assert lease_b.epoch > lease_a.epoch, "释放/过期后 epoch 必须单调递增"
    assert await a.acquire(TENANT, BOT) is None
    await b.release(lease_b)


async def test_expired_lease_is_taken_over_with_higher_epoch(redis: Redis) -> None:
    await redis.delete(
        f"connector:bot:{TENANT}:{BOT}", f"connector:bot:{TENANT}:{BOT}:epoch"
    )
    a = RedisLeaseManager(redis, owner_id="owner-a", ttl_sec=1.0)
    b = RedisLeaseManager(redis, owner_id="owner-b", ttl_sec=1.0)
    lease_a = await a.acquire(TENANT, BOT)
    assert lease_a is not None
    await asyncio.sleep(1.1)
    lease_b = await b.acquire(TENANT, BOT)
    assert lease_b is not None
    assert lease_b.epoch > lease_a.epoch
    assert await a.renew(lease_a) is None, "旧 owner 不得续租/继续发送"


async def test_reply_store_ttl_and_epoch_are_shared_across_processes(redis: Redis) -> None:
    writer = ReplyStore(redis, message_ttl_sec=1)
    reader = ReplyStore(redis, message_ttl_sec=1)
    await writer.remember_message(
        tenant_id=TENANT, bot_id=BOT, message_id="m-1", reply_ref="req-1", epoch=7
    )
    target = await reader.resolve_message(tenant_id=TENANT, bot_id=BOT, message_id="m-1")
    assert target is not None and target.reply_ref == "req-1" and target.epoch == 7
    ref = await reader.resolve_ref(tenant_id=TENANT, bot_id=BOT, reply_ref="req-1")
    assert ref is not None and ref.epoch == 7
    await writer.bind_run(
        tenant_id=TENANT,
        run_id="run-1",
        reply_ref="req-1",
        epoch=7,
        bot_id=BOT,
        external_user_id="user-1",
        external_conversation_id="chat-1",
    )
    run = await reader.resolve_run(tenant_id=TENANT, run_id="run-1")
    assert run is not None and run.reply_ref == "req-1"
    await asyncio.sleep(1.1)
    assert (
        await reader.resolve_message(tenant_id=TENANT, bot_id=BOT, message_id="m-1")
        is None
    ), "过期 reply_ref 必须显式消失（调用方据此降级），不能永远可用"


async def test_inbound_publish_is_deduplicated_and_redelivery_republishes_after_crash(
    redis: Redis,
) -> None:
    publisher = InboundPublisher(
        redis, shards=4, stream_maxlen=1000, lost_after_sec=1, pause_before_xadd_sec=0
    )
    event = _event("msg-dedupe")
    assert await publisher.publish(event) is PublishOutcome.PUBLISHED
    assert await publisher.publish(event) is PublishOutcome.DUPLICATE
    from muad_contracts.im import inbound_stream_key

    stream_key = inbound_stream_key(event, shards=4)
    assert await redis.xlen(stream_key) == 1

    # 模拟"标记已写、XADD 前崩溃"：RECEIVED 值；重投必须允许再次发布。
    key = f"connector:inbound:{TENANT}:{BOT}:msg-crash"
    await redis.set(key, f"RECEIVED:{time.time()}", ex=60)
    crash_event = _event("msg-crash")
    assert await publisher.publish(crash_event) is PublishOutcome.PUBLISHED
    assert (await redis.get(key) or "").startswith("PUBLISHED:")


async def test_inbound_sweep_marks_stale_received_as_lost_and_never_creates_run(
    redis: Redis, caplog: pytest.LogCaptureFixture
) -> None:
    publisher = InboundPublisher(
        redis, shards=4, stream_maxlen=1000, lost_after_sec=1, pause_before_xadd_sec=0
    )
    key = f"connector:inbound:{TENANT}:{BOT}:msg-lost"
    await redis.set(key, f"RECEIVED:{time.time() - 5}", ex=60)
    result = await publisher.sweep_lost()
    assert result.lost == 1
    assert (await redis.get(key) or "").startswith("LOST:")
    assert "connector_inbound_lost" in caplog.text
    # 没有 Stream 记录：没有用户可见 Run 被假装创建。
    assert await redis.xlen("im.inbound:0") == 0


@dataclass
class RecordingChannel:
    bot: str = BOT
    ready: bool = True
    replies: list[tuple[str, str]] | None = None
    streams: list[tuple[str, str, str, bool]] | None = None
    actives: list[str] | None = None
    artifacts: list[tuple[AttachmentRef, str | None]] | None = None

    def __post_init__(self) -> None:
        self.replies = []
        self.streams = []
        self.actives = []
        self.artifacts = []

    def has_bot(self, bot_id: str) -> bool:
        return bot_id == self.bot and self.ready

    def route_key(self, route: DeliveryRouteInput) -> str:
        return f"{route.bot_id}:{route.external_user_id}"

    async def send_active(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        assert self.actives is not None
        self.actives.append(message.text)

    async def send_reply_text(
        self, route: DeliveryRouteInput, reply_ref: str, text: str
    ) -> None:
        assert self.replies is not None
        self.replies.append((reply_ref, text))

    async def send_reply_stream(
        self,
        route: DeliveryRouteInput,
        reply_ref: str,
        stream_id: str,
        text: str,
        *,
        finish: bool,
    ) -> None:
        assert self.streams is not None
        self.streams.append((reply_ref, stream_id, text, finish))

    async def deliver_artifact_with_target(
        self,
        route: DeliveryRouteInput,
        artifact: AttachmentRef,
        *,
        tenant_id: str,
        run_id: str | None,
        reply_ref: str | None,
    ) -> tuple[str, str | None]:
        assert self.artifacts is not None
        self.artifacts.append((artifact, reply_ref))
        return "DELIVERED", None


async def test_text_with_valid_reply_ref_uses_in_session_reply(redis: Redis) -> None:
    manager, lease = await _lease(redis)
    replies = ReplyStore(redis)
    await replies.remember_message(
        tenant_id=TENANT, bot_id=BOT, message_id="m-1", reply_ref="req-1", epoch=lease.epoch
    )
    channel = RecordingChannel()
    executor = CommandExecutor(
        adapter=channel, leases=manager, replies=replies, owner_id=manager.owner_id
    )
    command = _command(
        DeliveryKind.TEXT, reply_ref="req-1", epoch=lease.epoch, text="answer"
    )
    result = await executor.execute(command)
    assert result.status is CommandStatus.DELIVERED
    assert channel.replies == [("req-1", "answer")]
    assert channel.actives == []


async def test_expired_reply_ref_degrades_to_active_send(redis: Redis) -> None:
    manager, lease = await _lease(redis)
    replies = ReplyStore(redis)
    channel = RecordingChannel()
    executor = CommandExecutor(
        adapter=channel, leases=manager, replies=replies, owner_id=manager.owner_id
    )
    command = _command(
        DeliveryKind.TEXT, reply_ref="req-expired", epoch=lease.epoch, text="answer"
    )
    result = await executor.execute(command)
    assert result.status is CommandStatus.DEGRADED
    assert result.code == "REPLY_REF_EXPIRED"
    assert channel.actives == ["answer"], "过期回调必须走主动发送，不能丢内容"
    assert channel.replies == [], "不得把消息挂到别的 req_id 上"


async def test_stale_epoch_is_degraded_and_not_sent_to_old_callback(redis: Redis) -> None:
    manager, lease = await _lease(redis, epoch=10)
    assert lease.epoch == 11
    replies = ReplyStore(redis)
    await replies.remember_message(
        tenant_id=TENANT, bot_id=BOT, message_id="m-1", reply_ref="req-1", epoch=lease.epoch
    )
    channel = RecordingChannel()
    executor = CommandExecutor(
        adapter=channel, leases=manager, replies=replies, owner_id=manager.owner_id
    )
    command = _command(
        DeliveryKind.TEXT, reply_ref="req-1", epoch=lease.epoch - 1, text="old"
    )
    result = await executor.execute(command)
    assert result.status is CommandStatus.DEGRADED
    assert channel.replies == []
    assert channel.actives == ["old"]


async def test_stream_deltas_without_reply_ref_are_buffered_and_sent_once(redis: Redis) -> None:
    manager, lease = await _lease(redis)
    channel = RecordingChannel()
    executor = CommandExecutor(
        adapter=channel,
        leases=manager,
        replies=ReplyStore(redis),
        owner_id=manager.owner_id,
    )
    run_id = uuid.uuid4()
    first = await executor.execute(
        _command(DeliveryKind.DELTA, run_id=run_id, text="你", epoch=lease.epoch)
    )
    second = await executor.execute(
        _command(DeliveryKind.DELTA, run_id=run_id, text="好", epoch=lease.epoch)
    )
    terminal = await executor.execute(
        _command(DeliveryKind.TERMINAL, run_id=run_id, text="！", epoch=lease.epoch)
    )
    assert first.status is CommandStatus.ACCEPTED
    assert second.status is CommandStatus.ACCEPTED
    assert terminal.status is CommandStatus.DELIVERED
    assert channel.actives == ["你好！"], "没有回调时只能在终态主动发送一次"


async def test_outbound_runner_consumes_command_and_writes_result(redis: Redis) -> None:
    manager, lease = await _lease(redis, ttl=10.0)
    replies = ReplyStore(redis)
    await replies.remember_message(
        tenant_id=TENANT, bot_id=BOT, message_id="m-1", reply_ref="req-1", epoch=lease.epoch
    )
    channel = RecordingChannel()
    executor = CommandExecutor(
        adapter=channel, leases=manager, replies=replies, owner_id=manager.owner_id
    )
    runner = OutboundRunner(
        redis, executor=executor, leases=manager, batch=8, max_age_sec=600, max_attempts=2
    )
    runner.set_bots(((TENANT, BOT),))
    command = _command(DeliveryKind.TEXT, reply_ref="req-1", epoch=lease.epoch, text="runner")
    await redis.xadd(
        f"im.outbound:{TENANT}:{BOT}",
        {"event": command.model_dump_json()},
        maxlen=1000,
        approximate=True,
    )
    task = asyncio.create_task(runner.run_forever())
    try:
        deadline = time.monotonic() + 5
        result_key = f"im:outbound:result:{command.command_id}"
        while time.monotonic() < deadline and not await redis.exists(result_key):
            await asyncio.sleep(0.05)
        assert await redis.exists(result_key), "OutboundRunner 必须回写结果"
        payload = await redis.get(result_key)
        assert payload is not None
        assert json.loads(payload)["status"] == "DELIVERED"
        assert channel.replies == [("req-1", "runner")]
        assert await redis.get(f"connector:outbound-cursor:{TENANT}:{BOT}")
    finally:
        runner.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.release(lease)


async def test_outbound_runner_fences_when_not_owner(redis: Redis) -> None:
    manager, lease = await _lease(redis, owner="owner-a", ttl=5.0)
    executor = CommandExecutor(
        adapter=RecordingChannel(),
        leases=manager,
        replies=ReplyStore(redis),
        owner_id="owner-b",
    )
    command = _command(DeliveryKind.TEXT, text="no")
    result = await executor.execute(command)
    assert result.status is CommandStatus.FENCED
    assert result.code == "LEASE_LOST"


async def test_route_of_preserves_external_conversation() -> None:
    command = _command(DeliveryKind.TEXT)
    route = route_of(command)
    assert route.external_conversation_id == "chat-1"
    assert route.bot_id == BOT


async def test_outbound_runner_deduplicates_same_command_id(redis: Redis) -> None:
    """同一条命令在 Stream 里出现两次（至少一次投递）：只允许执行一次。"""
    manager, lease = await _lease(redis, ttl=10.0)
    replies = ReplyStore(redis)
    await replies.remember_message(
        tenant_id=TENANT, bot_id=BOT, message_id="m-dup", reply_ref="req-dup", epoch=lease.epoch
    )
    channel = RecordingChannel()
    executor = CommandExecutor(
        adapter=channel, leases=manager, replies=replies, owner_id=manager.owner_id
    )
    runner = OutboundRunner(
        redis, executor=executor, leases=manager, batch=8, max_age_sec=600, max_attempts=2
    )
    runner.set_bots(((TENANT, BOT),))
    command = _command(
        DeliveryKind.TEXT, reply_ref="req-dup", epoch=lease.epoch, text="only-once"
    )
    for _ in range(2):
        await redis.xadd(
            f"im.outbound:{TENANT}:{BOT}",
            {"event": command.model_dump_json()},
            maxlen=1000,
            approximate=True,
        )
    task = asyncio.create_task(runner.run_forever())
    try:
        result_key = f"im:outbound:result:{command.command_id}"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not await redis.exists(result_key):
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        assert channel.replies == [("req-dup", "only-once")], (
            f"同 command_id 只允许发送一次；实际 {channel.replies}"
        )
    finally:
        runner.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.release(lease)


async def test_artifact_command_with_expired_reply_degrades(redis: Redis) -> None:
    manager, lease = await _lease(redis, ttl=10.0)
    channel = RecordingChannel()
    executor = CommandExecutor(
        adapter=channel, leases=manager, replies=ReplyStore(redis), owner_id=manager.owner_id
    )
    artifact = AttachmentRef(
        storage_key="run/r/a.pdf",
        kind="DOCUMENT",
        media_type="application/pdf",
        size=10,
        checksum="b" * 64,
        artifact_id=uuid.uuid4(),
    )
    command = DeliveryCommand(
        command_id=uuid.uuid4(),
        channel="WECOM",
        tenant_id=TENANT,
        bot_id=BOT,
        run_id=uuid.uuid4(),
        external_user_id="user-1",
        external_conversation_id="chat-1",
        kind=DeliveryKind.ARTIFACT,
        artifact=artifact,
        reply_ref="req-missing",
        epoch=lease.epoch,
        created_at=datetime.now(UTC),
    )
    result = await executor.execute(command)
    assert result.status is CommandStatus.DEGRADED
    assert result.code == "REPLY_REF_EXPIRED"
    assert channel.artifacts == [(artifact, None)], "过期回调时产物必须主动发送"


async def test_outbound_runner_retries_until_bot_connection_is_ready(redis: Redis) -> None:
    """bot 已拿到 lease 但连接还在握手：命令必须留在 Stream，不能写 FAILED。"""
    manager, lease = await _lease(redis, ttl=10.0)
    channel = RecordingChannel(ready=False)
    runner = OutboundRunner(
        redis,
        executor=CommandExecutor(
            adapter=channel,
            leases=manager,
            replies=ReplyStore(redis),
            owner_id=manager.owner_id,
        ),
        leases=manager,
        batch=8,
        max_age_sec=600,
        max_attempts=2,
    )
    runner.set_bots(((TENANT, BOT),))
    command = _command(DeliveryKind.TEXT, text="wait-connection", epoch=lease.epoch)
    await redis.xadd(
        f"im.outbound:{TENANT}:{BOT}",
        {"event": command.model_dump_json()},
        maxlen=1000,
        approximate=True,
    )
    task = asyncio.create_task(runner.run_forever())
    try:
        await asyncio.sleep(0.6)
        assert not await redis.exists(f"im:outbound:result:{command.command_id}")
        assert channel.actives == []
        channel.ready = True
        result_key = f"im:outbound:result:{command.command_id}"
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not await redis.exists(result_key):
            await asyncio.sleep(0.1)
        assert await redis.exists(result_key)
        assert json.loads(await redis.get(result_key))["status"] == "DELIVERED"
        assert channel.actives == ["wait-connection"]
    finally:
        runner.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.release(lease)


async def test_outbound_runner_lost_owner_does_not_write_result_or_cursor(redis: Redis) -> None:
    await redis.delete(
        f"connector:outbound-cursor:{TENANT}:{BOT}",
        f"im.outbound:{TENANT}:{BOT}",
        f"connector:bot:{TENANT}:{BOT}",
        f"connector:bot:{TENANT}:{BOT}:epoch",
    )
    owner = RedisLeaseManager(redis, owner_id="owner-a", ttl_sec=10.0)
    lease = await owner.acquire(TENANT, BOT)
    assert lease is not None
    loser = RedisLeaseManager(redis, owner_id="owner-b", ttl_sec=10.0)
    channel = RecordingChannel()
    runner = OutboundRunner(
        redis,
        executor=CommandExecutor(
            adapter=channel, leases=loser, replies=ReplyStore(redis), owner_id="owner-b"
        ),
        leases=loser,
        batch=8,
        max_age_sec=600,
        max_attempts=2,
    )
    runner.set_bots(((TENANT, BOT),))
    command = _command(DeliveryKind.TEXT, text="not-mine", epoch=lease.epoch)
    await redis.xadd(
        f"im.outbound:{TENANT}:{BOT}",
        {"event": command.model_dump_json()},
        maxlen=1000,
        approximate=True,
    )
    task = asyncio.create_task(runner.run_forever())
    try:
        await asyncio.sleep(0.6)
        assert not await redis.exists(f"im:outbound:result:{command.command_id}")
        assert not await redis.exists(f"connector:outbound-cursor:{TENANT}:{BOT}")
        assert channel.actives == []
    finally:
        runner.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await owner.release(lease)
