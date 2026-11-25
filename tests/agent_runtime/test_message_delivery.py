"""TASK-003：`runtime.message_delivery` + 通用租约 + 消息级投递泵。

真实边界（不 mock 存储或租约）：

- 隔离 PostgreSQL（`async_tool_datastore`）：真实表、唯一约束、行锁、PG 服务端时钟；
- 真实 `MessageDeliveryService` / `TenantMessageDelivery` 与 `DeliveryLeaseProtocol`；
- 真实 `MessageDeliveryPump`（只把 Gateway 发送器换成脚本化 sender）；
- 真实 Run 终态写入路径：`write_run_notification(..., push_messages=True)` 同事务写
  `message_delivery`，入箱行收窄为 `NOT_REQUIRED`（不再产生 channel push）。

覆盖：幂等入队、重复 reserve、崩溃接管/epoch fencing、超时 UNKNOWN、降级 DEGRADED、
NOT_SENT 退避、显式重发新 message_id、终态正文 + 多附件快照、UNKNOWN 不自动重发。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.channel_delivery import DeliveryPolicy
from muad_agent_runtime.application.channel_delivery_pump import DeliveryPumpPolicy
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_NOT_REQUIRED,
    KIND_RUN_COMPLETED,
    KIND_RUN_WAITING_INPUT,
    run_terminal_delivery_key,
    run_waiting_delivery_key,
    write_run_notification,
)
from muad_agent_runtime.application.message_delivery import (
    MESSAGE_DEGRADED,
    MESSAGE_DELIVERED,
    MESSAGE_FAILED,
    MESSAGE_PENDING,
    MESSAGE_UNKNOWN,
    MessageDeliveryPayload,
    MessageDeliveryService,
    TenantMessageDelivery,
    delivery_target_key,
    enqueue_message_delivery,
    run_terminal_message_key,
)
from muad_agent_runtime.application.message_delivery_pump import (
    DELIVERY_PAYLOAD_INVALID,
    MessageDeliveryIntent,
    MessageDeliveryPump,
    MessageSendOutcome,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    ChannelNotification,
    Conversation,
    MessageDelivery,
    MessageDeliveryAttempt,
    RunRecord,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import AttachmentRef, ChannelContext, DeliveryRouteInput, RunStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

ENTRY_ID = uuid.uuid4()
BOT_ID = "bot-message-delivery"
EXTERNAL_USER_ID = "external-message-delivery"
FINAL_TEXT = "终态正文：文件已生成"
BACKOFF_BASE_SEC = 5
_SEQ = count(1)


@dataclass(frozen=True, slots=True)
class MessageTenant:
    tenant_id: str
    agent_id: uuid.UUID
    user_id: uuid.UUID


class MessageSettings:
    def __init__(self, *, backoff_base_sec: int = BACKOFF_BASE_SEC) -> None:
        self._backoff_base_sec = backoff_base_sec

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "task": {"delivery_backoff_base_sec": self._backoff_base_sec},
                "webchat": {"notification_retention_days": 30},
            },
        )


@pytest.fixture
async def delivery_runtime(
    async_tool_datastore: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    await runtime_db.dispose_engine()
    try:
        yield runtime_db.get_session_factory()
    finally:
        await runtime_db.dispose_engine()


@pytest.fixture
def tenant() -> MessageTenant:
    return MessageTenant(
        tenant_id=f"message-{uuid.uuid4().hex[:12]}",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _route() -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id=EXTERNAL_USER_ID,
        external_conversation_id="thread-1",
    )


def _attachment(filename: str = "result.txt") -> AttachmentRef:
    return AttachmentRef(
        storage_key=f"artifacts/{uuid.uuid4().hex}.txt",
        kind="DOCUMENT",
        media_type="text/plain",
        size=12,
        filename=filename,
        checksum="sha256:" + uuid.uuid4().hex,
        artifact_id=uuid.uuid4(),
    )


def _policy() -> DeliveryPolicy:
    return DeliveryPolicy(backoff_base_sec=BACKOFF_BASE_SEC, replay_retention_days=30)


def _pump_policy(*, max_attempts: int = 1, timeout_sec: float = 1.0) -> DeliveryPumpPolicy:
    return DeliveryPumpPolicy(
        concurrency=1,
        timeout_sec=timeout_sec,
        lease_sec=5,
        poll_sec=0.01,
        batch_size=8,
        retry_base_sec=0.01,
        retry_cap_sec=0.02,
        max_attempts=max_attempts,
    )


class ScriptedSender:
    """记录发出去的意图，按脚本返回结局（可模拟超时/降级）。"""

    def __init__(self, outcomes: list[MessageSendOutcome] | None = None) -> None:
        self._outcomes = list(outcomes or [MessageSendOutcome(MESSAGE_DELIVERED)])
        self.intents: list[MessageDeliveryIntent] = []

    async def send(
        self,
        intent: MessageDeliveryIntent,
        *,
        attempt_id: uuid.UUID,
        lease_epoch: int,
    ) -> MessageSendOutcome:
        self.intents.append(intent)
        if not self._outcomes:
            return MessageSendOutcome(MESSAGE_DELIVERED)
        return self._outcomes.pop(0)


class SleepingSender:
    async def send(
        self,
        intent: MessageDeliveryIntent,
        *,
        attempt_id: uuid.UUID,
        lease_epoch: int,
    ) -> MessageSendOutcome:
        await asyncio.sleep(0.2)
        return MessageSendOutcome(MESSAGE_DELIVERED)


@dataclass(frozen=True, slots=True)
class _Enqueued:
    id: uuid.UUID
    message_key: str
    target_key: str
    owner_id: uuid.UUID


async def _enqueue(
    factory: async_sessionmaker[AsyncSession],
    tenant: MessageTenant,
    *,
    key: str | None = None,
    target_key: str | None = None,
    text: str = "正文",
    attachments: tuple[AttachmentRef, ...] = (),
) -> _Enqueued:
    message_key = key or f"run:{uuid.uuid4()}:terminal"
    owner_id = uuid.uuid4()
    async with factory() as session, session.begin():
        await enqueue_message_delivery(
            session,
            tenant_id=tenant.tenant_id,
            owner_type="RUN",
            owner_id=owner_id,
            message_key=message_key,
            kind="TERMINAL",
            target_key=target_key or delivery_target_key(_route()),
            route=_route(),
            payload=MessageDeliveryPayload(
                text=text, attachments=attachments, status="COMPLETED"
            ),
            agent_id=tenant.agent_id,
            platform_user_id=tenant.user_id,
        )
        row = await session.scalar(
            sa.select(MessageDelivery).where(
                MessageDelivery.tenant_id == tenant.tenant_id,
                MessageDelivery.message_key == message_key,
            )
        )
    assert row is not None
    return _Enqueued(
        id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        owner_id=owner_id,
    )


async def _row(
    factory: async_sessionmaker[AsyncSession], delivery_id: uuid.UUID
) -> MessageDelivery:
    async with factory() as session:
        row = await session.get(MessageDelivery, delivery_id)
    assert row is not None
    return row


async def _attempts(
    factory: async_sessionmaker[AsyncSession], delivery_id: uuid.UUID
) -> list[MessageDeliveryAttempt]:
    async with factory() as session:
        rows = await session.scalars(
            sa.select(MessageDeliveryAttempt)
            .where(MessageDeliveryAttempt.delivery_id == delivery_id)
            .order_by(MessageDeliveryAttempt.attempt_no)
        )
        return list(rows)


@dataclass(frozen=True, slots=True)
class _RunRef:
    """`write_run_notification` 需要的最小 Run 事实（脱离 Session 后仍可读）。"""

    id: uuid.UUID
    tenant_id: str
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    input_message_id: str | None
    channel_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _RunSeed:
    conversation_id: uuid.UUID
    run: _RunRef
    terminal_event_id: uuid.UUID
    interrupt_id: uuid.UUID


async def _seed_run(
    factory: async_sessionmaker[AsyncSession],
    tenant: MessageTenant,
    *,
    attachments: tuple[AttachmentRef, ...] = (),
) -> _RunSeed:
    now = datetime.now(UTC)
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    terminal_event_id = uuid.uuid4()
    channel_json = ChannelContext(
        type="WECOM",
        entry_id=ENTRY_ID,
        bot_id=BOT_ID,
        external_user_id=EXTERNAL_USER_ID,
        external_conversation_id="thread-1",
    ).model_dump(mode="json")
    async with factory() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=2,
                channel_account_id=ENTRY_ID,
                last_message_at=now,
            )
        )
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.user_id,
                agent_id=tenant.agent_id,
                status=RunStatus.COMPLETED,
                input_text="seed",
                input_message_id="msg-message-delivery",
                trace_id=f"message-delivery-{uuid.uuid4().hex}",
                start_time=now,
                deadline_at=now + timedelta(seconds=600),
                channel_json=channel_json,
            )
        )
        await session.flush()
        seq = next(_SEQ)
        session.add(
            CanonicalEvent(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                event_type="ASSISTANT_MESSAGE",
                payload_json={
                    "text": FINAL_TEXT,
                    "attachments": [
                        item.model_dump(mode="json") for item in attachments
                    ],
                },
            )
        )
        session.add(
            CanonicalEvent(
                id=terminal_event_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq + 1,
                stream_type="run.completed",
                event_type="RUN_COMPLETED",
                payload_json={
                    "status": "COMPLETED",
                    "final_text": FINAL_TEXT,
                },
            )
        )
    return _RunSeed(
        conversation_id=conversation_id,
        run=_RunRef(
            id=run_id,
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            user_id=tenant.user_id,
            agent_id=tenant.agent_id,
            input_message_id="msg-message-delivery",
            channel_json=channel_json,
        ),
        terminal_event_id=terminal_event_id,
        interrupt_id=uuid.uuid4(),
    )


# --------------------------------------------------------------------------------------
# 1. 幂等入队与稳定身份
# --------------------------------------------------------------------------------------


async def test_enqueue_is_idempotent_for_the_same_message_and_target(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    message_key = f"run:{uuid.uuid4()}:terminal"
    first = await _enqueue(delivery_runtime, tenant, key=message_key)
    async with delivery_runtime() as session, session.begin():
        second = await enqueue_message_delivery(
            session,
            tenant_id=tenant.tenant_id,
            owner_type="RUN",
            owner_id=first.owner_id,
            message_key=message_key,
            kind="TERMINAL",
            target_key=first.target_key,
            route=_route(),
            payload=MessageDeliveryPayload(text="另一份正文", status="COMPLETED"),
        )
    assert second.id == first.id and second.created is False
    async with delivery_runtime() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(MessageDelivery)
            .where(
                MessageDelivery.tenant_id == tenant.tenant_id,
                MessageDelivery.message_key == message_key,
            )
        )
    assert count == 1
    assert delivery_target_key(_route()) == first.target_key


# --------------------------------------------------------------------------------------
# 2. reserve/renew/ack：降级、幂等回执、失约即停手
# --------------------------------------------------------------------------------------


async def test_degraded_is_success_and_replayed_without_resending(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    row = await _enqueue(delivery_runtime, tenant)
    service = MessageDeliveryService(runtime_db.get_session_factory, _policy())
    attempt_id = uuid.uuid4()
    lease = await service.reserve(
        tenant_id=tenant.tenant_id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=attempt_id,
        lease_sec=5,
    )
    assert lease.state == "DELIVERING" and lease.lease_epoch == 1
    renewed = await service.renew(
        tenant_id=tenant.tenant_id,
        delivery_id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=attempt_id,
        epoch=lease.lease_epoch,
        lease_sec=5,
    )
    assert renewed.lease_until is not None and renewed.lease_until >= lease.lease_until

    confirmation = await service.ack(
        tenant_id=tenant.tenant_id,
        delivery_id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=attempt_id,
        epoch=lease.lease_epoch,
        outcome="DEGRADED",
        result={"fallback_url": "https://example.test/f/1", "code": "ARTIFACT_DEGRADED"},
    )
    assert confirmation.state == MESSAGE_DEGRADED
    stored = await _row(delivery_runtime, row.id)
    assert stored.status == MESSAGE_DEGRADED
    assert stored.fallback_url == "https://example.test/f/1"
    assert stored.delivered_at is not None
    attempts = await _attempts(delivery_runtime, row.id)
    assert [item.status for item in attempts] == [MESSAGE_DEGRADED]

    replay = await service.reserve(
        tenant_id=tenant.tenant_id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=uuid.uuid4(),
        lease_sec=5,
    )
    assert replay.lease_until is None
    assert replay.receipt is not None and replay.receipt.outcome == "DEGRADED"

    repeated = await service.ack(
        tenant_id=tenant.tenant_id,
        delivery_id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=attempt_id,
        epoch=1,
        outcome="DEGRADED",
        result={"fallback_url": "https://example.test/f/1"},
    )
    assert repeated.reused is True
    assert (await _row(delivery_runtime, row.id)).status == MESSAGE_DEGRADED


async def test_epoch_fencing_and_crash_takeover(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    row = await _enqueue(delivery_runtime, tenant)
    service = MessageDeliveryService(runtime_db.get_session_factory, _policy())
    first, second = uuid.uuid4(), uuid.uuid4()
    results = await asyncio.gather(
        service.reserve(
            tenant_id=tenant.tenant_id,
            message_key=row.message_key,
            target_key=row.target_key,
            attempt_id=first,
            lease_sec=5,
        ),
        service.reserve(
            tenant_id=tenant.tenant_id,
            message_key=row.message_key,
            target_key=row.target_key,
            attempt_id=second,
            lease_sec=5,
        ),
        return_exceptions=True,
    )
    granted = [item for item in results if not isinstance(item, Exception)]
    denied = [item for item in results if isinstance(item, Exception)]
    assert len(granted) == 1 and len(denied) == 1
    assert isinstance(denied[0], AppError)
    assert denied[0].code == ErrorCode.DELIVERY_LEASE_LOST
    # 找到持有租约的 attempt：重放同一 attempt 不推进 epoch，另一 attempt 被拒。
    stored = await _row(delivery_runtime, row.id)
    holder = first if stored.lease_owner == str(first) else second
    loser = second if holder == first else first

    # 模拟崩溃：租约过期后新 attempt 接管，epoch/attempt 单调前进。
    async with delivery_runtime() as session, session.begin():
        await session.execute(
            sa.update(MessageDelivery)
            .where(MessageDelivery.id == row.id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    takeover = uuid.uuid4()
    taken = await service.reserve(
        tenant_id=tenant.tenant_id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=takeover,
        lease_sec=5,
    )
    assert taken.lease_epoch == 2 and taken.attempt == 2

    with pytest.raises(AppError) as stale:
        await service.ack(
            tenant_id=tenant.tenant_id,
            delivery_id=row.id,
            message_key=row.message_key,
            target_key=row.target_key,
            attempt_id=loser,
            epoch=1,
            outcome="DELIVERED",
            result=None,
        )
    assert stale.value.code == ErrorCode.DELIVERY_LEASE_LOST
    assert (await _row(delivery_runtime, row.id)).status == "DELIVERING"


async def test_not_sent_backs_off_and_unknown_is_not_auto_resent(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    service = MessageDeliveryService(runtime_db.get_session_factory, _policy())
    row = await _enqueue(delivery_runtime, tenant)
    attempt_id = uuid.uuid4()
    lease = await service.reserve(
        tenant_id=tenant.tenant_id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=attempt_id,
        lease_sec=5,
    )
    pending = await service.ack(
        tenant_id=tenant.tenant_id,
        delivery_id=row.id,
        message_key=row.message_key,
        target_key=row.target_key,
        attempt_id=attempt_id,
        epoch=lease.lease_epoch,
        outcome="NOT_SENT",
        result={"reason": "connect_refused"},
    )
    assert pending.state == MESSAGE_PENDING
    stored = await _row(delivery_runtime, row.id)
    assert stored.next_attempt_at > datetime.now(UTC)
    assert [item.status for item in await _attempts(delivery_runtime, row.id)] == [
        MESSAGE_PENDING
    ]

    unknown_row = await _enqueue(delivery_runtime, tenant)
    unknown_attempt = uuid.uuid4()
    unknown_lease = await service.reserve(
        tenant_id=tenant.tenant_id,
        message_key=unknown_row.message_key,
        target_key=unknown_row.target_key,
        attempt_id=unknown_attempt,
        lease_sec=5,
    )
    await service.ack(
        tenant_id=tenant.tenant_id,
        delivery_id=unknown_row.id,
        message_key=unknown_row.message_key,
        target_key=unknown_row.target_key,
        attempt_id=unknown_attempt,
        epoch=unknown_lease.lease_epoch,
        outcome="UNKNOWN",
        result={"reason": "timeout"},
    )
    # 即使到期时间已过，UNKNOWN 也不进入领取集合，reserve 也不再授租约。
    async with delivery_runtime() as session, session.begin():
        await session.execute(
            sa.update(MessageDelivery)
            .where(MessageDelivery.id == unknown_row.id)
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    pump = MessageDeliveryPump(
        runtime_db.get_session_factory,
        TenantMessageDelivery(runtime_db.get_session_factory, MessageSettings()),
        ScriptedSender(),
        _pump_policy(),
    )
    assert await pump.claim_due(limit=10) == []
    replay = await service.reserve(
        tenant_id=tenant.tenant_id,
        message_key=unknown_row.message_key,
        target_key=unknown_row.target_key,
        attempt_id=uuid.uuid4(),
        lease_sec=5,
    )
    assert replay.lease_until is None and replay.state == MESSAGE_UNKNOWN


async def test_explicit_resend_uses_a_new_message_key(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    run_id = uuid.uuid4()
    first = await _enqueue(
        delivery_runtime, tenant, key=run_terminal_message_key(run_id)
    )
    # 显式“再发一次”是新的消息身份：同一 Run 的 message_id 不同，允许再次发送。
    resend_key = f"run:{run_id}:message:{uuid.uuid4()}"
    second = await _enqueue(delivery_runtime, tenant, key=resend_key)
    assert first.id != second.id
    assert first.message_key != second.message_key


# --------------------------------------------------------------------------------------
# 3. 投递泵：真实领取 + 多附件快照 + 超时 UNKNOWN
# --------------------------------------------------------------------------------------


async def test_pump_delivers_payload_with_multiple_attachments(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    first, second = _attachment("a.txt"), _attachment("b.txt")
    row = await _enqueue(
        delivery_runtime,
        tenant,
        text=FINAL_TEXT,
        attachments=(first, second),
    )
    sender = ScriptedSender([MessageSendOutcome(MESSAGE_DELIVERED)])
    pump = MessageDeliveryPump(
        runtime_db.get_session_factory,
        TenantMessageDelivery(runtime_db.get_session_factory, MessageSettings()),
        sender,
        _pump_policy(),
    )
    assert await pump.run_once() == 1
    await pump.drain()

    assert len(sender.intents) == 1
    intent = sender.intents[0]
    assert intent.payload.text == FINAL_TEXT
    assert [item.artifact_id for item in intent.payload.attachments] == [
        first.artifact_id,
        second.artifact_id,
    ]
    stored = await _row(delivery_runtime, row.id)
    assert stored.status == MESSAGE_DELIVERED
    assert stored.delivered_at is not None
    assert [item.status for item in await _attempts(delivery_runtime, row.id)] == [
        MESSAGE_DELIVERED
    ]


async def test_pump_quarantines_invalid_payload_without_blocking_valid_batch(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    bad_id = uuid.uuid4()
    async with delivery_runtime() as session, session.begin():
        session.add(
            MessageDelivery(
                id=bad_id,
                tenant_id=tenant.tenant_id,
                owner_type="RUN",
                owner_id=uuid.uuid4(),
                message_key=f"run:{uuid.uuid4()}:terminal",
                kind="TERMINAL",
                target_key=delivery_target_key(_route()),
                route_json=_route().model_dump(mode="json"),
                payload_json={},
                status=MESSAGE_PENDING,
                agent_id=tenant.agent_id,
                platform_user_id=tenant.user_id,
            )
        )
    good = await _enqueue(delivery_runtime, tenant)
    sender = ScriptedSender()
    pump = MessageDeliveryPump(
        runtime_db.get_session_factory,
        TenantMessageDelivery(runtime_db.get_session_factory, MessageSettings()),
        sender,
        _pump_policy(),
    )

    claimed = await pump.claim_due(limit=10)

    assert [intent.delivery_id for intent in claimed] == [good.id], (
        "坏载荷只能淘汰自己，不能阻塞同批合法投递"
    )
    bad = await _row(delivery_runtime, bad_id)
    assert bad.status == MESSAGE_FAILED
    assert bad.last_error_code == DELIVERY_PAYLOAD_INVALID
    assert bad.attempt_count == 0, "没有真实发送尝试，不能伪造 attempt"
    assert bad.last_error_message
    assert await _attempts(delivery_runtime, bad_id) == []

    assert [intent.delivery_id for intent in await pump.claim_due(limit=10)] == [good.id]
    assert await pump.run_once() == 1
    await pump.drain()
    assert (await _row(delivery_runtime, good.id)).status == MESSAGE_DELIVERED


async def test_pump_timeout_becomes_unknown_and_stops_retrying(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    row = await _enqueue(delivery_runtime, tenant)
    pump = MessageDeliveryPump(
        runtime_db.get_session_factory,
        TenantMessageDelivery(runtime_db.get_session_factory, MessageSettings()),
        SleepingSender(),
        _pump_policy(timeout_sec=0.05, max_attempts=1),
    )
    assert await pump.run_once() == 1
    await pump.drain()

    stored = await _row(delivery_runtime, row.id)
    assert stored.status == MESSAGE_UNKNOWN
    assert stored.last_error_code == "timeout"
    assert stored.attempt_count == 1
    assert await pump.claim_due(limit=10) == []


# --------------------------------------------------------------------------------------
# 4. Run 终态写入路径：message_delivery 是唯一 Run push
# --------------------------------------------------------------------------------------


async def test_run_terminal_push_is_written_to_message_delivery_not_channel_push(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    attachment = _attachment("report.pdf")
    seed = await _seed_run(delivery_runtime, tenant, attachments=(attachment,))
    async with delivery_runtime() as session, session.begin():
        receipt = await write_run_notification(
            session,
            run=seed.run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=run_terminal_delivery_key(seed.run.id),
            event_id=seed.terminal_event_id,
            push_messages=True,
        )
        row = await session.scalar(
            sa.select(MessageDelivery).where(
                MessageDelivery.tenant_id == tenant.tenant_id,
                MessageDelivery.message_key == run_terminal_message_key(seed.run.id),
            )
        )
        notification = await session.get(ChannelNotification, receipt.id) if receipt else None
    assert row is not None
    assert row.owner_type == "RUN" and row.owner_id == seed.run.id
    assert row.payload_json["text"] == FINAL_TEXT
    assert row.payload_json["attachments"][0]["artifact_id"] == str(attachment.artifact_id)
    assert row.message_id == "msg-message-delivery"
    assert row.agent_id == tenant.agent_id
    assert notification is not None
    assert notification.delivery_state == DELIVERY_NOT_REQUIRED

    # 同一终态重放：message_delivery 幂等，不新增行。
    async with delivery_runtime() as session, session.begin():
        await write_run_notification(
            session,
            run=seed.run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=run_terminal_delivery_key(seed.run.id),
            event_id=seed.terminal_event_id,
            push_messages=True,
        )
    async with delivery_runtime() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(MessageDelivery)
            .where(
                MessageDelivery.tenant_id == tenant.tenant_id,
                MessageDelivery.message_key == run_terminal_message_key(seed.run.id),
            )
        )
    assert count == 1


async def test_run_waiting_push_is_human_question_with_stable_key(
    delivery_runtime: async_sessionmaker[AsyncSession], tenant: MessageTenant
) -> None:
    seed = await _seed_run(delivery_runtime, tenant)
    async with delivery_runtime() as session, session.begin():
        await write_run_notification(
            session,
            run=seed.run,
            kind=KIND_RUN_WAITING_INPUT,
            delivery_key=run_waiting_delivery_key(seed.run.id, seed.interrupt_id),
            event_id=seed.terminal_event_id,
            push_messages=True,
        )
        row = await session.scalar(
            sa.select(MessageDelivery).where(
                MessageDelivery.tenant_id == tenant.tenant_id,
                MessageDelivery.owner_id == seed.run.id,
                MessageDelivery.kind == "HUMAN_QUESTION",
            )
        )
    assert row is not None
    assert row.message_key == f"run:{seed.run.id}:message:{seed.interrupt_id}"
    assert row.status == MESSAGE_PENDING
