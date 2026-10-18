"""B-265 / TASK-065：出站投递的 reserve/renew/ack 持久协议（design §3.3/§3.4/§3.6）。

真实边界（不 mock 存储或租约）：

- **隔离 PostgreSQL**（`async_tool_datastore`：每用例一个空库 + 迁移到 head，跑完 drop）：
  真实 `runtime.channel_notification` 行、真实 `(tenant_id,delivery_key)` 唯一约束、真实行锁
  与真实 PG 服务端时钟（租约/退避/回执时刻都以它裁决）；
- **真实 Runtime 业务服务**：`ChannelDeliveryService` 与 API-18 端口（真实 `main.app` 装配、
  `require_internal_service` 门控、真实响应封套、真实 SQL）；
- **真实通知写入路径（TASK-063）**：`PENDING` 出站意图由 `write_run_notification` 写进同一张
  表、用同一稳定投递键（`run:{run_id}:terminal`）——本协议是它的下一段状态转换；
- **真实并发**：两个独立事务同时 reserve（恰一个拿到租约），以及旁路连接持有行锁时
  reserve **确实阻塞**（不是靠运气串行）。

关键断言（B-265）：

1. reserve 按稳定 delivery_key 授**有界**租约（PENDING→DELIVERING、`lease_owner`=attempt、
   `lease_epoch`=1、`lease_until`≈PG now+租期）；**存在占位不等于成功**（无任何回执）；
2. 重复 reserve（同 attempt、租约有效）不重复投递（epoch/期限/attempt 全不变）；不同 attempt
   对有效租约 reserve ⇒ `DELIVERY_LEASE_LOST`；
3. 过期租约回收 + epoch 防重：租约过期后新 attempt 接管 ⇒ epoch+1/attempt+1；旧 attempt 的
   renew/ack（epoch 落后）⇒ `DELIVERY_LEASE_LOST`（失约即停手，迟到 ack 不改写结果）；
4. renew 核 attempt+epoch 且期限只前进不倒退；失约（过期/接管）一律 `DELIVERY_LEASE_LOST`；
5. **只有实际交付成功才 ack**：DELIVERED 写回执；ack 幂等（重复回放**首次**结果，不重写）；
   `NOT_SENT` 回 PENDING + 有界退避（未送达不是成功）；`UNKNOWN` 保留核实、不再授租约；
6. **已送达重放首次结果**：租约清除后 reserve 仍能取回首次结果（不重发）；超过回执重放保留期
   ⇒ `DELIVERY_RECEIPT_EXPIRED`；
7. `NOT_REQUIRED` 是明确状态（不是出站意图）：reserve ⇒ `COMMON_CONFLICT`，无租约的
   renew/ack ⇒ `DELIVERY_LEASE_LOST`；
8. 身份与租户边界：未知 delivery_key、错 id、错 key、跨租户一律 `COMMON_NOT_FOUND`；缺服务
   身份 ⇒ 403；设置源不可读 ⇒ 显式失败且**不推进**任何投递状态（网络在事务外）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any, get_args

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.api.channel_delivery import ChannelDeliveryPort
from muad_agent_runtime.api.deps import get_platform_settings_client
from muad_agent_runtime.application.channel_delivery import (
    DELIVERY_DELIVERED,
    DELIVERY_DELIVERING,
    DELIVERY_UNKNOWN,
    ChannelDeliveryService,
    DeliveryLease,
    DeliveryPolicy,
)
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_NOT_REQUIRED,
    DELIVERY_PENDING,
    DELIVERY_STATES,
    KIND_RUN_COMPLETED,
    run_terminal_delivery_key,
    write_run_notification,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.models.runtime import (
    ChannelNotification,
    ChannelNotificationFeed,
    Conversation,
    RunRecord,
)
from muad_api import AppError
from muad_common import SharedSettings
from muad_contracts import RunStatus
from muad_contracts.channel_delivery import (
    DeliveryAck,
    DeliveryLeaseRenewal,
    DeliveryReservationRequest,
    DeliveryState,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = pytest.mark.integration

RESERVE_PATH = "/internal/channel-notifications/reserve"
ENTRY_ID = uuid.uuid4()
LEASE_SEC = 30
BACKOFF_BASE_SEC = 5
_SEQ = count(1)


@dataclass(frozen=True, slots=True)
class DeliveryTenant:
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class SeededNotification:
    notification_id: uuid.UUID
    delivery_key: str
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    user_id: uuid.UUID
    entry_id: uuid.UUID
    seq: int


# --------------------------------------------------------------------------------------
# 隔离 PostgreSQL / 真实 Runtime 进程环境 / 真实 HTTP 应用
# --------------------------------------------------------------------------------------


@pytest.fixture
async def delivery_tenant(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[DeliveryTenant]:
    """隔离 PostgreSQL 号位 + Runtime 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    await runtime_db.dispose_engine()
    try:
        yield DeliveryTenant(
            tenant_id=f"delivery-{uuid.uuid4().hex[:12]}",
            agent_id=uuid.uuid4(),
            platform_user_id=uuid.uuid4(),
        )
    finally:
        await runtime_db.dispose_engine()


@pytest.fixture
def database_url(async_tool_datastore: dict[str, str]) -> str:
    """旁路连接（锁持有者）的库地址。"""
    return async_tool_datastore["database_url"]


class DeliverySettings:
    """平台设置源（Console 权威）：可注入退避基数与回执重放保留期，并记录取用次数。"""

    def __init__(self, *, retention_days: int = 30, backoff_base_sec: int = BACKOFF_BASE_SEC) -> None:
        self._retention_days = retention_days
        self._backoff_base_sec = backoff_base_sec
        self.calls = 0

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        self.calls += 1
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "task": {"delivery_backoff_base_sec": self._backoff_base_sec},
                "webchat": {"notification_retention_days": self._retention_days},
            },
        )


class BrokenSettings:
    """设置源不可读：显式失败，绝不回落到默认值。"""

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        raise AppError("PLATFORM_SETTINGS_UNAVAILABLE")


class LockProbingSettings(DeliverySettings):
    """设置快照在**事务外**取用：取快照时用另一条连接对同一行做 `FOR UPDATE NOWAIT` 探测。

    若调用方此时已经持有该行锁（把设置读取放进了事务内），`NOWAIT` 会直接报锁不可用；
    探测成功即证明"网络在事务外"。
    """

    def __init__(self, notification_id: uuid.UUID) -> None:
        super().__init__()
        self._notification_id = notification_id
        self.observed_unlocked = False

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        async with runtime_db.get_session_factory()() as session:
            await session.execute(
                sa.text(
                    "SELECT id FROM runtime.channel_notification WHERE id = :id FOR UPDATE NOWAIT"
                ).bindparams(id=self._notification_id)
            )
        self.observed_unlocked = True
        return await super().fetch_snapshot(tenant_id=tenant_id, trace_id=trace_id)


@pytest.fixture
def settings() -> DeliverySettings:
    return DeliverySettings()


@pytest.fixture
def policy() -> DeliveryPolicy:
    return DeliveryPolicy(backoff_base_sec=BACKOFF_BASE_SEC, replay_retention_days=30)


@pytest.fixture
def service(policy: DeliveryPolicy) -> ChannelDeliveryService:
    return ChannelDeliveryService(runtime_db.get_session_factory, policy)


@pytest.fixture
async def client(
    delivery_tenant: DeliveryTenant, settings: DeliverySettings
) -> AsyncIterator[AsyncClient]:
    """真实 Runtime ASGI 应用（真实路由 + 服务身份门控 + 真实响应封套）。"""
    from muad_agent_runtime.main import app

    app.dependency_overrides[get_platform_settings_client] = lambda: settings
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
    finally:
        app.dependency_overrides.pop(get_platform_settings_client, None)


def _headers(tenant: DeliveryTenant) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant.tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


# --------------------------------------------------------------------------------------
# 真实行种子与读取（只写/读本用例的隔离库）
# --------------------------------------------------------------------------------------


async def _seed_run(
    tenant: DeliveryTenant, *, entry_id: uuid.UUID | None = ENTRY_ID
) -> tuple[uuid.UUID, uuid.UUID]:
    """真实会话 + 终态 Run（通知行的物理 FK 事实）。"""
    now = datetime.now(UTC)
    run_id, conversation_id, seq = uuid.uuid4(), uuid.uuid4(), next(_SEQ)
    async with runtime_db.get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=0,
                channel_account_id=entry_id,
                last_message_at=now,
            )
        )
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status=RunStatus.COMPLETED,
                input_text="seed",
                trace_id=f"delivery-seed-{seq}",
                start_time=now,
                deadline_at=now + timedelta(seconds=600),
            )
        )
        await session.commit()
    return run_id, conversation_id


async def _seed_notification(
    tenant: DeliveryTenant,
    *,
    delivery_key: str | None = None,
    state: str = DELIVERY_PENDING,
    attempt: int = 0,
    lease_owner: str | None = None,
    lease_epoch: int = 0,
    lease_until: datetime | None = None,
    delivery_result: dict[str, Any] | None = None,
    entry_id: uuid.UUID | None = ENTRY_ID,
) -> SeededNotification:
    """一条真实通知行（真实 feed/会话/Run 物理 FK；投递列按用例需要摆放）。"""
    run_id, conversation_id = await _seed_run(tenant, entry_id=entry_id)
    now = datetime.now(UTC)
    seq = next(_SEQ)
    key = delivery_key or run_terminal_delivery_key(run_id)
    async with runtime_db.get_session_factory()() as session:
        # feed 是稳定注册行（每个用户一行）：同租户多条通知复用同一行。
        await session.execute(
            pg_insert(ChannelNotificationFeed)
            .values(tenant_id=tenant.tenant_id, user_id=tenant.platform_user_id)
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id"])
        )
        feed_id = await session.scalar(
            sa.select(ChannelNotificationFeed.id).where(
                ChannelNotificationFeed.tenant_id == tenant.tenant_id,
                ChannelNotificationFeed.user_id == tenant.platform_user_id,
            )
        )
        assert feed_id is not None
        row = ChannelNotification(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            feed_id=feed_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=tenant.platform_user_id,
            entry_id=entry_id or uuid.uuid4(),
            kind=KIND_RUN_COMPLETED,
            seq=seq,
            delivery_key=key,
            delivery_state=state,
            attempt=attempt,
            next_attempt_at=now,
            lease_owner=lease_owner,
            lease_epoch=lease_epoch,
            lease_until=lease_until,
            delivery_result=delivery_result,
        )
        session.add(row)
        await session.commit()
        return SeededNotification(
            notification_id=row.id,
            delivery_key=key,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=tenant.platform_user_id,
            entry_id=entry_id or row.entry_id,
            seq=seq,
        )


async def _row(notification_id: uuid.UUID) -> dict[str, Any]:
    """独立会话读取真实行事实（断言不依赖服务返回值）。"""
    async with runtime_db.get_session_factory()() as session:
        result = (
            await session.execute(
                sa.text(
                    "SELECT delivery_state, attempt, lease_owner, lease_epoch, lease_until,"
                    " delivery_result, next_attempt_at, update_time"
                    " FROM runtime.channel_notification WHERE id = :id"
                ).bindparams(id=notification_id)
            )
        ).mappings().one()
    return dict(result)


async def _expire_lease(notification_id: uuid.UUID) -> None:
    """真实行更新：把租约期限回拨到过去（不改实现里的时间判断）。"""
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.channel_notification"
                " SET lease_until = now() - interval '1 second', update_time = now()"
                " WHERE id = :id"
            ).bindparams(id=notification_id)
        )
        await session.commit()


async def _shift_acked_at(notification_id: uuid.UUID, *, days_ago: int) -> None:
    """真实行更新：把冻结的首次结果时刻移到保留期之外。"""
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.channel_notification SET delivery_result = jsonb_set("
                " delivery_result, '{acked_at}', to_jsonb(to_char("
                " now() - make_interval(days => :days), 'YYYY-MM-DD\"T\"HH24:MI:SS.US+00:00'))),"
                " update_time = now() WHERE id = :id"
            ).bindparams(id=notification_id, days=days_ago)
        )
        await session.commit()


async def _seconds_from_now(notification_id: uuid.UUID, column: str) -> float:
    """真实 PG 时钟下 `column - now()` 的秒数（租约/退避的有界性证据）。"""
    if column not in ("lease_until", "next_attempt_at"):
        raise ValueError(f"unsupported column: {column}")
    async with runtime_db.get_session_factory()() as session:
        remaining = await session.scalar(
            sa.text(
                f"SELECT extract(epoch FROM ({column} - now()))"
                " FROM runtime.channel_notification WHERE id = :id"
            ).bindparams(id=notification_id)
        )
    return float(remaining)


async def _hold_row_lock(database_url: str, notification_id: uuid.UUID) -> tuple[Any, AsyncConnection, Any]:
    """旁路连接持有通知行锁（证明 reserve 真的排在行锁上）。"""
    engine = create_async_engine(database_url)
    connection = await engine.connect()
    transaction = await connection.begin()
    await connection.execute(
        sa.text("SELECT id FROM runtime.channel_notification WHERE id = :id FOR UPDATE").bindparams(
            id=notification_id
        )
    )
    return engine, connection, transaction


async def _reserve(
    service: ChannelDeliveryService,
    tenant: DeliveryTenant,
    key: str,
    *,
    attempt_id: uuid.UUID | None = None,
    lease_sec: int | None = None,
) -> DeliveryLease:
    return await service.reserve(
        tenant_id=tenant.tenant_id,
        delivery_key=key,
        attempt_id=attempt_id or uuid.uuid4(),
        lease_sec=lease_sec,
    )


async def _fails(coro: Any) -> str:
    """跑一个必失败的操作并回传稳定错误码（非 AppError 直接冒出来，不吞异常）。"""
    with pytest.raises(AppError) as failure:
        await coro
    return str(failure.value.code)


# --------------------------------------------------------------------------------------
# 状态词表与 0026 的 CHECK 对齐（协议词表的权威是数据库约束）
# --------------------------------------------------------------------------------------


async def test_b265_delivery_state_vocabulary_matches_the_persisted_check() -> None:
    """线协议/服务词表与 0026 的 CHECK 同词表：任何一处漂移都要在验收里失败。"""
    expected = {
        DELIVERY_PENDING,
        DELIVERY_DELIVERING,
        DELIVERY_DELIVERED,
        DELIVERY_UNKNOWN,
        DELIVERY_NOT_REQUIRED,
    }
    assert set(DELIVERY_STATES) == expected
    assert set(get_args(DeliveryState)) == expected
    async with runtime_db.get_session_factory()() as session:
        definition = await session.scalar(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_channel_notification_delivery_state'"
            )
        )
    assert definition is not None
    assert all(f"'{state}'" in definition for state in expected)


# --------------------------------------------------------------------------------------
# B-265：reserve / renew / ack 的持久协议
# --------------------------------------------------------------------------------------


async def test_b265_reserve_grants_a_bounded_lease_and_a_placeholder_is_not_success(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """PENDING→DELIVERING：租约是有界栅栏；授出租约本身不是送达（无任何回执）。"""
    seed = await _seed_notification(delivery_tenant)
    attempt = uuid.uuid4()

    lease = await _reserve(
        service, delivery_tenant, seed.delivery_key, attempt_id=attempt, lease_sec=LEASE_SEC
    )

    assert lease.state == DELIVERY_DELIVERING
    assert lease.lease_epoch == 1 and lease.attempt == 1
    assert lease.reused is False and lease.receipt is None
    assert lease.notification_id == seed.notification_id
    row = await _row(seed.notification_id)
    assert row["delivery_state"] == DELIVERY_DELIVERING
    assert row["lease_owner"] == str(attempt) and row["lease_epoch"] == 1
    assert row["attempt"] == 1
    # 期限与 PG 服务端时钟同源：约等于 now()+租期（本机墙钟不参与裁决）。
    assert LEASE_SEC - 3 <= await _seconds_from_now(seed.notification_id, "lease_until") <= LEASE_SEC + 3
    # 存在占位不等于成功：没有任何回执被写下。
    assert row["delivery_result"] is None


async def test_b265_repeated_reserve_is_idempotent_and_other_attempts_are_fenced(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """重复 reserve 不重复投递；别人持有有效租约时只能停手。"""
    seed = await _seed_notification(delivery_tenant)
    attempt = uuid.uuid4()
    first = await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=attempt)
    before = await _row(seed.notification_id)

    again = await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=attempt)

    assert again.reused is True
    assert (again.lease_epoch, again.lease_until, again.attempt) == (
        first.lease_epoch,
        first.lease_until,
        first.attempt,
    )
    assert await _row(seed.notification_id) == before  # 期限/计数/时刻逐字段不变

    assert await _fails(_reserve(service, delivery_tenant, seed.delivery_key)) == "DELIVERY_LEASE_LOST"
    assert await _row(seed.notification_id) == before


async def test_b265_concurrent_reserves_grant_exactly_one_lease(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """真实 PG 并发：两个独立事务同时 reserve，恰一个拿到租约（不双重投递）。"""
    seed = await _seed_notification(delivery_tenant)
    attempts = (uuid.uuid4(), uuid.uuid4())

    results = await asyncio.gather(
        *(_reserve(service, delivery_tenant, seed.delivery_key, attempt_id=a) for a in attempts),
        return_exceptions=True,
    )

    grants = [(attempts[i], r) for i, r in enumerate(results) if isinstance(r, DeliveryLease)]
    refusals = [r for r in results if isinstance(r, AppError)]
    assert len(grants) == 1, results
    assert [str(r.code) for r in refusals] == ["DELIVERY_LEASE_LOST"]
    winner, grant = grants[0]
    assert grant.lease_epoch == 1 and grant.attempt == 1
    row = await _row(seed.notification_id)
    assert row["delivery_state"] == DELIVERY_DELIVERING
    assert row["lease_owner"] == str(winner) and row["lease_epoch"] == 1 and row["attempt"] == 1


async def test_b265_reserve_waits_on_the_notification_row_lock(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService, database_url: str
) -> None:
    """真实行锁证据：旁路连接持锁期间 reserve 阻塞，放锁后才拿到租约（epoch 仍从 1 起）。"""
    seed = await _seed_notification(delivery_tenant)
    engine, connection, transaction = await _hold_row_lock(database_url, seed.notification_id)
    task = asyncio.create_task(_reserve(service, delivery_tenant, seed.delivery_key))
    try:
        await asyncio.sleep(0.4)
        assert task.done() is False, "reserve 没有阻塞在通知行锁上"
        await transaction.rollback()
    finally:
        await connection.close()
        await engine.dispose()

    lease = await asyncio.wait_for(task, timeout=10)
    assert lease.lease_epoch == 1 and lease.state == DELIVERY_DELIVERING


async def test_b265_expired_lease_is_reclaimed_with_a_new_epoch_and_the_old_attempt_stops(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """过期租约回收（epoch+1）+ epoch 防重：旧 attempt 的迟到确认不能改写结果。"""
    seed = await _seed_notification(delivery_tenant)
    old, new = uuid.uuid4(), uuid.uuid4()
    granted = await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=old)
    assert granted.lease_epoch == 1

    await _expire_lease(seed.notification_id)

    assert (
        await _fails(
            service.renew(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                delivery_key=seed.delivery_key,
                attempt_id=old,
                epoch=1,
            )
        )
        == "DELIVERY_LEASE_LOST"
    )
    assert (
        await _fails(
            service.ack(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                delivery_key=seed.delivery_key,
                attempt_id=old,
                epoch=1,
                outcome="DELIVERED",
                result={"delivered": True},
            )
        )
        == "DELIVERY_LEASE_LOST"
    )
    assert (await _row(seed.notification_id))["delivery_result"] is None

    reclaimed = await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=new)

    assert reclaimed.reused is False and reclaimed.lease_epoch == 2 and reclaimed.attempt == 2
    row = await _row(seed.notification_id)
    assert row["lease_owner"] == str(new) and row["lease_epoch"] == 2
    # 被接管的旧 attempt 迟到确认：epoch 防重，结果不被改写。
    assert (
        await _fails(
            service.ack(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                delivery_key=seed.delivery_key,
                attempt_id=old,
                epoch=1,
                outcome="DELIVERED",
                result={"delivered": True},
            )
        )
        == "DELIVERY_LEASE_LOST"
    )
    assert (await _row(seed.notification_id))["delivery_result"] is None


async def test_b265_renew_requires_the_live_lease_and_only_moves_forward(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """续租核 attempt+epoch 且租约必须仍有效；期限只前进不倒退。"""
    seed = await _seed_notification(delivery_tenant)
    attempt = uuid.uuid4()
    granted = await _reserve(
        service, delivery_tenant, seed.delivery_key, attempt_id=attempt, lease_sec=10
    )

    async def _renew(*, attempt_id: uuid.UUID, epoch: int, lease_sec: int = LEASE_SEC) -> DeliveryLease:
        return await service.renew(
            tenant_id=delivery_tenant.tenant_id,
            notification_id=seed.notification_id,
            delivery_key=seed.delivery_key,
            attempt_id=attempt_id,
            epoch=epoch,
            lease_sec=lease_sec,
        )

    assert await _fails(_renew(attempt_id=uuid.uuid4(), epoch=1)) == "DELIVERY_LEASE_LOST"
    assert await _fails(_renew(attempt_id=attempt, epoch=2)) == "DELIVERY_LEASE_LOST"

    extended = await _renew(attempt_id=attempt, epoch=1, lease_sec=60)
    assert extended.lease_until > granted.lease_until
    shortened = await _renew(attempt_id=attempt, epoch=1, lease_sec=5)
    assert shortened.lease_until == extended.lease_until  # 只前进不倒退
    assert await _seconds_from_now(seed.notification_id, "lease_until") > 55

    await _expire_lease(seed.notification_id)
    assert await _fails(_renew(attempt_id=attempt, epoch=1)) == "DELIVERY_LEASE_LOST"


async def test_b265_ack_delivered_freezes_the_first_result_and_replays_it(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """只有实际发送成功写 DELIVERED；租约清除后首次结果仍可恢复，重复 ack 不重写。"""
    seed = await _seed_notification(delivery_tenant)
    attempt = uuid.uuid4()
    await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=attempt)
    first_result = {
        "accepted": True,
        "delivered": True,
        "outcome": "DEGRADED",
        "fallback_url": "https://example.invalid/artifact",
    }

    confirmation = await service.ack(
        tenant_id=delivery_tenant.tenant_id,
        notification_id=seed.notification_id,
        delivery_key=seed.delivery_key,
        attempt_id=attempt,
        epoch=1,
        outcome="DELIVERED",
        result=first_result,
    )

    assert confirmation.state == DELIVERY_DELIVERED and confirmation.reused is False
    assert confirmation.receipt is not None
    assert confirmation.receipt.outcome == "DELIVERED"
    assert confirmation.receipt.result == first_result
    row = await _row(seed.notification_id)
    assert row["delivery_state"] == DELIVERY_DELIVERED
    assert row["lease_owner"] is None and row["lease_until"] is None  # 租约清除
    assert row["delivery_result"]["result"] == first_result

    # 已送达重放首次结果：再次 reserve 不重发、不新发号。
    replay = await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=uuid.uuid4())
    assert replay.state == DELIVERY_DELIVERED and replay.reused is True
    assert replay.lease_until is None and replay.lease_epoch == 0
    assert replay.receipt is not None and replay.receipt.result == first_result
    assert (await _row(seed.notification_id))["attempt"] == 1

    # ack 幂等：重复确认回放首次结果；换个结果也不覆盖首次结果。
    again = await service.ack(
        tenant_id=delivery_tenant.tenant_id,
        notification_id=seed.notification_id,
        delivery_key=seed.delivery_key,
        attempt_id=attempt,
        epoch=1,
        outcome="DELIVERED",
        result={"accepted": True, "delivered": True, "outcome": "DELIVERED"},
    )
    assert again.reused is True and again.receipt is not None
    assert again.receipt.result == first_result
    assert (await _row(seed.notification_id))["delivery_result"]["result"] == first_result
    # 别人的迟到确认没有位置：没有租约就不再是租约持有者。
    assert (
        await _fails(
            service.ack(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                delivery_key=seed.delivery_key,
                attempt_id=uuid.uuid4(),
                epoch=1,
                outcome="DELIVERED",
                result={"delivered": True},
            )
        )
        == "DELIVERY_LEASE_LOST"
    )


async def test_b265_ack_not_sent_returns_to_pending_with_bounded_backoff(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """确定没发出去 ⇒ 回 PENDING 并退避重试；未送达永远不是成功。"""
    seed = await _seed_notification(delivery_tenant)
    first_attempt = uuid.uuid4()
    await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=first_attempt)

    confirmation = await service.ack(
        tenant_id=delivery_tenant.tenant_id,
        notification_id=seed.notification_id,
        delivery_key=seed.delivery_key,
        attempt_id=first_attempt,
        epoch=1,
        outcome="NOT_SENT",
        result={"error": "gateway rejected"},
    )

    assert confirmation.state == DELIVERY_PENDING
    assert confirmation.receipt is not None and confirmation.receipt.outcome == "NOT_SENT"
    row = await _row(seed.notification_id)
    assert row["delivery_state"] == DELIVERY_PENDING
    assert row["lease_owner"] is None and row["lease_until"] is None
    first_wait = await _seconds_from_now(seed.notification_id, "next_attempt_at")
    assert BACKOFF_BASE_SEC - 1 <= first_wait <= BACKOFF_BASE_SEC + 3

    second_attempt = uuid.uuid4()
    retry = await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=second_attempt)
    assert retry.reused is False and retry.attempt == 2 and retry.lease_epoch == 2
    await service.ack(
        tenant_id=delivery_tenant.tenant_id,
        notification_id=seed.notification_id,
        delivery_key=seed.delivery_key,
        attempt_id=second_attempt,
        epoch=retry.lease_epoch,
        outcome="NOT_SENT",
    )
    assert await _seconds_from_now(seed.notification_id, "next_attempt_at") > first_wait  # 指数退避


async def test_b265_unknown_is_retained_for_verification_and_never_resent(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """没拿到结论 ⇒ UNKNOWN 保留核实：不自动重发、不再授租约、不标已送达。"""
    seed = await _seed_notification(delivery_tenant)
    attempt = uuid.uuid4()
    await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=attempt)

    confirmation = await service.ack(
        tenant_id=delivery_tenant.tenant_id,
        notification_id=seed.notification_id,
        delivery_key=seed.delivery_key,
        attempt_id=attempt,
        epoch=1,
        outcome="UNKNOWN",
        result={"error": "timeout after send"},
    )

    assert confirmation.state == DELIVERY_UNKNOWN
    row = await _row(seed.notification_id)
    assert row["delivery_state"] == DELIVERY_UNKNOWN
    assert row["lease_owner"] is None and row["delivery_result"]["outcome"] == "UNKNOWN"

    retained = await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=uuid.uuid4())
    assert retained.state == DELIVERY_UNKNOWN and retained.reused is True
    assert retained.lease_until is None and retained.lease_epoch == 0
    assert retained.receipt is not None and retained.receipt.outcome == "UNKNOWN"
    after = await _row(seed.notification_id)
    assert after["attempt"] == 1 and after["lease_owner"] is None  # 没有新租约、没有新尝试
    assert (
        await _fails(
            service.renew(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                delivery_key=seed.delivery_key,
                attempt_id=attempt,
                epoch=1,
            )
        )
        == "DELIVERY_LEASE_LOST"
    )


async def test_b265_not_required_is_not_an_outbound_intent(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """网页持久入箱（NOT_REQUIRED）是明确状态：不会被误当成待发消息。"""
    seed = await _seed_notification(delivery_tenant, state=DELIVERY_NOT_REQUIRED)
    before = await _row(seed.notification_id)

    assert await _fails(_reserve(service, delivery_tenant, seed.delivery_key)) == "COMMON_CONFLICT"
    assert await _row(seed.notification_id) == before
    assert (
        await _fails(
            service.ack(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                delivery_key=seed.delivery_key,
                attempt_id=uuid.uuid4(),
                epoch=0,
                outcome="DELIVERED",
                result={"delivered": True},
            )
        )
        == "DELIVERY_LEASE_LOST"
    )


async def test_b265_receipt_replay_expires_beyond_the_retention_window(
    delivery_tenant: DeliveryTenant, policy: DeliveryPolicy
) -> None:
    """超过回执重放保留期的旧 delivery_key 返回过期语义，不当成新投递。"""
    service = ChannelDeliveryService(runtime_db.get_session_factory, policy)
    seed = await _seed_notification(delivery_tenant)
    attempt = uuid.uuid4()
    await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=attempt)
    await service.ack(
        tenant_id=delivery_tenant.tenant_id,
        notification_id=seed.notification_id,
        delivery_key=seed.delivery_key,
        attempt_id=attempt,
        epoch=1,
        outcome="DELIVERED",
        result={"delivered": True},
    )
    before = await _row(seed.notification_id)
    await _shift_acked_at(seed.notification_id, days_ago=31)

    assert await _fails(_reserve(service, delivery_tenant, seed.delivery_key)) == "DELIVERY_RECEIPT_EXPIRED"
    after = await _row(seed.notification_id)
    assert after["delivery_state"] == DELIVERY_DELIVERED
    assert after["lease_owner"] is None and after["attempt"] == before["attempt"]


async def test_b265_delivery_identity_and_tenant_are_enforced(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """身份边界：未知键/错 id/错键/跨租户一律 NOT_FOUND（不泄露存在性）。"""
    seed = await _seed_notification(delivery_tenant)
    other = await _seed_notification(
        delivery_tenant, delivery_key=f"run:{uuid.uuid4()}:terminal"
    )

    assert (
        await _fails(_reserve(service, delivery_tenant, f"run:{uuid.uuid4()}:terminal"))
        == "COMMON_NOT_FOUND"
    )
    assert (
        await _fails(
            service.renew(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=other.notification_id,
                delivery_key=seed.delivery_key,
                attempt_id=uuid.uuid4(),
                epoch=1,
            )
        )
        == "COMMON_NOT_FOUND"
    )
    attempt = uuid.uuid4()
    await _reserve(service, delivery_tenant, seed.delivery_key, attempt_id=attempt)
    assert (
        await _fails(
            service.ack(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                delivery_key=other.delivery_key,
                attempt_id=attempt,
                epoch=1,
                outcome="DELIVERED",
                result={"delivered": True},
            )
        )
        == "COMMON_NOT_FOUND"
    )
    # 跨租户：同一个键在别的租户下存在，也不可见（租户谓词在 WHERE 内）。
    stranger = DeliveryTenant(
        tenant_id=f"delivery-{uuid.uuid4().hex[:12]}",
        agent_id=uuid.uuid4(),
        platform_user_id=uuid.uuid4(),
    )
    assert await _fails(_reserve(service, stranger, seed.delivery_key)) == "COMMON_NOT_FOUND"


async def test_b265_reserves_the_pending_intent_written_by_the_notification_path(
    delivery_tenant: DeliveryTenant, service: ChannelDeliveryService
) -> None:
    """与 TASK-063 的写入路径衔接：同事务写下的 PENDING 意图用同一稳定键被本协议领取。"""
    run_id, _ = await _seed_run(delivery_tenant)
    key = run_terminal_delivery_key(run_id)
    async with runtime_db.get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        receipt = await write_run_notification(
            session,
            run=run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=key,
            delivery_state=DELIVERY_PENDING,
        )
        await session.commit()
    assert receipt is not None and receipt.created is True
    assert (await _row(receipt.id))["delivery_state"] == DELIVERY_PENDING

    lease = await _reserve(service, delivery_tenant, key, attempt_id=uuid.uuid4())

    assert lease.state == DELIVERY_DELIVERING and lease.lease_epoch == 1
    assert lease.notification_id == receipt.id


# --------------------------------------------------------------------------------------
# HTTP：API-18 的真实装配、服务身份门控与响应封套
# --------------------------------------------------------------------------------------


async def test_b265_api18_requires_service_identity_and_walks_the_protocol(
    delivery_tenant: DeliveryTenant, client: AsyncClient
) -> None:
    """真实路由 + 门控 + 封套：缺身份 403；reserve/renew/ack 走完整协议。"""
    seed = await _seed_notification(delivery_tenant)
    attempt = uuid.uuid4()
    body = {"delivery_key": seed.delivery_key, "attempt_id": str(attempt), "lease_sec": LEASE_SEC}

    for headers in (
        {"X-Tenant-Id": delivery_tenant.tenant_id},
        {"X-Tenant-Id": delivery_tenant.tenant_id, "X-Internal-Service": "wrong"},
    ):
        refused = await client.post(RESERVE_PATH, headers=headers, json=body)
        assert refused.status_code == 403
    assert (await _row(seed.notification_id))["delivery_state"] == DELIVERY_PENDING

    granted = await client.post(RESERVE_PATH, headers=_headers(delivery_tenant), json=body)
    assert granted.status_code == 200, granted.text
    data = granted.json()["data"]
    assert granted.json()["code"] == "0"
    assert data["notification_id"] == str(seed.notification_id)
    assert data["state"] == DELIVERY_DELIVERING and data["lease_epoch"] == 1 and data["attempt"] == 1
    assert data["receipt"] is None

    renewed = await client.post(
        f"/internal/channel-notifications/{seed.notification_id}/renew",
        headers=_headers(delivery_tenant),
        json={**body, "epoch": 1, "lease_sec": 60},
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["data"]["lease_epoch"] == 1

    acked = await client.post(
        f"/internal/channel-notifications/{seed.notification_id}/ack",
        headers=_headers(delivery_tenant),
        json={
            "delivery_key": seed.delivery_key,
            "attempt_id": str(attempt),
            "epoch": 1,
            "outcome": "DELIVERED",
            "result": {"delivered": True, "outcome": "DELIVERED"},
        },
    )
    assert acked.status_code == 200, acked.text
    ack_data = acked.json()["data"]
    assert ack_data["state"] == DELIVERY_DELIVERED and ack_data["reused"] is False
    assert ack_data["receipt"]["outcome"] == "DELIVERED"
    assert ack_data["receipt"]["result"] == {"delivered": True, "outcome": "DELIVERED"}

    replayed = await client.post(RESERVE_PATH, headers=_headers(delivery_tenant), json=body)
    assert replayed.status_code == 200
    assert replayed.json()["data"]["state"] == DELIVERY_DELIVERED
    assert replayed.json()["data"]["reused"] is True
    assert replayed.json()["data"]["receipt"]["result"]["delivered"] is True

    missing = await client.post(
        f"/internal/channel-notifications/{uuid.uuid4()}/renew",
        headers=_headers(delivery_tenant),
        json={**body, "epoch": 1},
    )
    assert missing.status_code == 404 and missing.json()["code"] == "COMMON_NOT_FOUND"
    invalid = await client.post(
        RESERVE_PATH, headers=_headers(delivery_tenant), json={**body, "lease_sec": 1}
    )
    assert invalid.status_code == 422
    assert (await _row(seed.notification_id))["delivery_state"] == DELIVERY_DELIVERED


async def test_b265_settings_source_is_read_per_call_and_failures_do_not_advance_state(
    delivery_tenant: DeliveryTenant, settings: DeliverySettings
) -> None:
    """设置快照在事务外按次取用：源不可读 ⇒ 显式失败且不推进任何投递状态。"""
    seed = await _seed_notification(delivery_tenant)
    port = ChannelDeliveryPort(runtime_db.get_session_factory, settings)
    attempt = uuid.uuid4()
    body = DeliveryReservationRequest(
        delivery_key=seed.delivery_key, attempt_id=attempt, lease_sec=LEASE_SEC
    )

    granted = await port.reserve(tenant_id=delivery_tenant.tenant_id, body=body)
    assert settings.calls == 1 and granted.state == DELIVERY_DELIVERING
    confirmation = await port.ack(
        tenant_id=delivery_tenant.tenant_id,
        notification_id=seed.notification_id,
        body=DeliveryAck(
            delivery_key=seed.delivery_key,
            attempt_id=attempt,
            epoch=granted.lease_epoch,
            outcome="DELIVERED",
            result={"delivered": True},
        ),
    )
    assert settings.calls == 2 and confirmation.state == DELIVERY_DELIVERED
    # 已送达后没有租约：续租必须停手（每次操作各取一次设置，之后才判租约）。
    assert (
        await _fails(
            port.renew(
                tenant_id=delivery_tenant.tenant_id,
                notification_id=seed.notification_id,
                body=DeliveryLeaseRenewal(
                    delivery_key=seed.delivery_key, attempt_id=attempt, epoch=granted.lease_epoch
                ),
            )
        )
        == "DELIVERY_LEASE_LOST"
    )
    assert settings.calls == 3

    broken = ChannelDeliveryPort(runtime_db.get_session_factory, BrokenSettings())
    second = await _seed_notification(delivery_tenant)
    before = await _row(second.notification_id)
    assert (
        await _fails(
            broken.reserve(
                tenant_id=delivery_tenant.tenant_id,
                body=DeliveryReservationRequest(
                    delivery_key=second.delivery_key, attempt_id=uuid.uuid4()
                ),
            )
        )
        == "PLATFORM_SETTINGS_UNAVAILABLE"
    )
    assert await _row(second.notification_id) == before

    # "网络在事务外"：设置快照取用时调用方尚未持有该行锁（NOWAIT 探测成功）。
    probing = LockProbingSettings(second.notification_id)
    probe_port = ChannelDeliveryPort(runtime_db.get_session_factory, probing)
    probed = await probe_port.reserve(
        tenant_id=delivery_tenant.tenant_id,
        body=DeliveryReservationRequest(
            delivery_key=second.delivery_key, attempt_id=uuid.uuid4(), lease_sec=LEASE_SEC
        ),
    )
    assert probing.observed_unlocked is True
    assert probed.state == DELIVERY_DELIVERING
