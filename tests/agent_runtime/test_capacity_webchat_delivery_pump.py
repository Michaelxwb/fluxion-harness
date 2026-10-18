"""B-266 / TASK-066：Runtime 独立可靠交付泵（design §3.2/§3.6/§4.1）。

真实边界（不 mock 存储或租约）：

- **隔离 PostgreSQL**（`async_tool_datastore`：每用例一个空库 + 迁移到 head，跑完 drop）：
  真实 `runtime.channel_notification` 行、真实 `(tenant_id,delivery_key)` 唯一约束、真实
  `ix_channel_notification_due` partial 索引（领取谓词的执行计划）与真实 PG 服务端时钟
  （到期、租约、退避全部由它裁决）；
- **真实 Runtime 业务服务**：`ChannelDeliveryPump` + TASK-065 的 `ChannelDeliveryService`
  （经 `TenantChannelDelivery` 按租户取设置快照）走生产代码；`PENDING` 意图由 TASK-063 的
  `write_run_notification` 写进同一张表；
- **真实 HTTP 交付边界**：uvicorn + 真实 socket 扮演既有 Gateway `/internal/deliveries`
  （真实请求体、真实响应封套、真实 404/超时/连接拒绝/慢发送），必要时 HTTP 探针就是"网关
  下线/重启"本身——泵不知道对端是谁，只按真实结论分类；
- **真实执行槽**：`ExecutionSupervisor` 槽全忙时交付照常推进（泵不占执行槽）。

关键断言（B-266 契约）：

1. 到期领取走 `ix_channel_notification_due`：只领 `PENDING` + `next_attempt_at` 到期 +
   未超尝试预算的行；未到期/超预算/`NOT_REQUIRED` 一律不领；
2. 真实交付到**原入口**：稳定 `delivery_key` + Run 持久化的入站路由 + 终态正文经真实
   HTTP 发到 Gateway；成功才 `DELIVERED`（送达不代替已读）；
3. 无空闲投递槽不领取（`领取数量≤空闲投递槽`）；
4. `NOT_SENT` 回 `PENDING` + 退避（退避期内不再领取），退避到期后按同一稳定键恢复交付；
5. `UNKNOWN` 保留核实：不自动重发、不再进入到期集合；
6. 慢发送期间真实续租（`lease_until` 前进）；
7. 失约即停手：租约被接管后旧 attempt 不写任何结果（不 ack、不标送达）；
8. 限次：尝试预算耗尽后不再领取、不再发送；
9. Gateway 重启（连接拒绝）后按持久意图恢复交付，且执行槽耗尽也可投递；
10. 装配根挂载：lifespan 注入启动预算并起独立循环，退出即停（不增加第五部署单元）。
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.channel_delivery import TenantChannelDelivery
from muad_agent_runtime.application.channel_delivery_pump import (
    ChannelDeliveryPump,
    DeliveryPumpPolicy,
    GatewayDeliverySender,
    due_intents_statement,
)
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_NOT_REQUIRED,
    DELIVERY_PENDING,
    KIND_RUN_COMPLETED,
    run_terminal_delivery_key,
    write_run_notification,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.gateway_delivery_client import GatewayDeliveryClient
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    ChannelNotification,
    ChannelNotificationFeed,
    Conversation,
    RunRecord,
)
from muad_common import SharedSettings
from muad_contracts import ChannelContext, RunStatus
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

ENTRY_ID = uuid.uuid4()
BOT_ID = "bot-wecom-1"
EXTERNAL_USER_ID = "external-user-1"
EXTERNAL_THREAD_ID = "external-thread-1"
FINAL_TEXT = "真实终态正文：任务已完成"
INTERNAL_TOKEN = "pump-internal-token"
BACKOFF_BASE_SEC = 5
WAIT_TIMEOUT_SEC = 15.0
_SEQ = count(1)


@dataclass(frozen=True, slots=True)
class DeliveryTenant:
    tenant_id: str
    agent_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class SeededIntent:
    notification_id: uuid.UUID
    delivery_key: str
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    event_id: uuid.UUID
    tenant_id: str
    user_id: uuid.UUID


# --------------------------------------------------------------------------------------
# 隔离 PostgreSQL / Runtime 进程环境 / 真实平台设置源
# --------------------------------------------------------------------------------------


@pytest.fixture
async def delivery_runtime(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """隔离 PostgreSQL/Redis 号位 + Runtime 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)
    await runtime_db.dispose_engine()
    try:
        yield runtime_db.get_session_factory()
    finally:
        await runtime_db.dispose_engine()


@pytest.fixture
def delivery_tenant() -> DeliveryTenant:
    return DeliveryTenant(
        tenant_id=f"pump-{uuid.uuid4().hex[:12]}",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


class DeliverySettings:
    """平台设置源（Console 权威）：退避基数与回执保留期的唯一来源，并记录取用次数。"""

    def __init__(self, *, backoff_base_sec: int = BACKOFF_BASE_SEC, retention_days: int = 30) -> None:
        self._backoff_base_sec = backoff_base_sec
        self._retention_days = retention_days
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


@pytest.fixture
def settings_source() -> DeliverySettings:
    return DeliverySettings()


# --------------------------------------------------------------------------------------
# 真实 HTTP 探针：既有 Gateway `/internal/deliveries`
# --------------------------------------------------------------------------------------


@dataclass
class ProbeState:
    mode: str = "ok"
    hold: asyncio.Event | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)


def _probe_app(state: ProbeState) -> FastAPI:
    """Gateway 探针：真实响应封套 + 真实失败形态（拒绝/挂住/放行）。"""
    app = FastAPI()

    @app.post("/internal/deliveries")
    async def deliver(request: Request) -> Any:
        body = await request.json()
        state.requests.append({"headers": dict(request.headers), "body": body})
        if state.mode == "reject":
            return JSONResponse(
                {"code": "BOT_NOT_FOUND", "msg": "bot missing", "data": None}, status_code=404
            )
        if state.mode == "hang":
            await asyncio.sleep(5)
        if state.mode == "gated" and state.hold is not None:
            await state.hold.wait()
        return JSONResponse(
            {
                "code": "0",
                "msg": "ok",
                "data": {
                    "accepted": True,
                    "delivered": True,
                    "deduplicated": False,
                    "duplicate": False,
                },
            }
        )

    return app


class GatewayProbe:
    """真实 uvicorn + 真实 socket 的交付探针；`stop()/start()` 就是"网关重启"。"""

    def __init__(self) -> None:
        self.state = ProbeState()
        self.port: int | None = None
        self._server: uvicorn.Server | None = None
        self._serving: asyncio.Task[None] | None = None
        self._listener: socket.socket | None = None

    @property
    def url(self) -> str:
        assert self.port is not None
        return f"http://127.0.0.1:{self.port}"

    @property
    def hits(self) -> int:
        return len(self.state.requests)

    async def start(self) -> None:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port or 0))
        listener.listen()
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._server = uvicorn.Server(
            uvicorn.Config(
                _probe_app(self.state),
                log_level="error",
                lifespan="off",
                timeout_graceful_shutdown=1,
            )
        )
        self._serving = asyncio.create_task(self._server.serve(sockets=[listener]))
        for _ in range(300):
            if self._server.started:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("gateway probe did not start")

    async def stop(self) -> None:
        if self._server is not None:
            # 强制退出：挂住的探针请求（"没拿到结论"用例）不阻塞"网关下线"本身。
            self._server.should_exit = True
            self._server.force_exit = True
        if self._serving is not None:
            await asyncio.wait_for(self._serving, timeout=10)
        if self._listener is not None:
            self._listener.close()
        self._server, self._serving, self._listener = None, None, None


@pytest.fixture
async def gateway_probe() -> AsyncIterator[GatewayProbe]:
    probe = GatewayProbe()
    await probe.start()
    try:
        yield probe
    finally:
        await probe.stop()


# --------------------------------------------------------------------------------------
# 泵装配：真实客户端 + 真实按租户设置快照的协议门面
# --------------------------------------------------------------------------------------


def pump_policy(**overrides: Any) -> DeliveryPumpPolicy:
    values: dict[str, Any] = {
        "concurrency": 2,
        "timeout_sec": 1.0,
        "lease_sec": 5,
        "poll_sec": 0.05,
        "batch_size": 8,
        "retry_base_sec": 0.05,
        "retry_cap_sec": 0.2,
        "max_attempts": 3,
    }
    values.update(overrides)
    return DeliveryPumpPolicy(**values)


@pytest.fixture
async def pump_builder(
    delivery_runtime: async_sessionmaker[AsyncSession], settings_source: DeliverySettings
) -> AsyncIterator[Callable[..., ChannelDeliveryPump]]:
    clients: list[GatewayDeliveryClient] = []

    def build(probe: GatewayProbe, policy: DeliveryPumpPolicy) -> ChannelDeliveryPump:
        client = GatewayDeliveryClient(
            probe.url, service_token=INTERNAL_TOKEN, timeout_sec=policy.timeout_sec
        )
        clients.append(client)
        return ChannelDeliveryPump(
            runtime_db.get_session_factory,
            TenantChannelDelivery(runtime_db.get_session_factory, settings_source),
            GatewayDeliverySender(client),
            policy,
        )

    try:
        yield build
    finally:
        for client in clients:
            await client.aclose()


# --------------------------------------------------------------------------------------
# 真实行种子与读取（只写/读本用例的隔离库）
# --------------------------------------------------------------------------------------


def _channel_json() -> dict[str, Any]:
    return ChannelContext(
        type="WECOM",
        bot_id=BOT_ID,
        external_user_id=EXTERNAL_USER_ID,
        external_conversation_id=EXTERNAL_THREAD_ID,
    ).model_dump(mode="json")


async def _seed_intent(
    factory: async_sessionmaker[AsyncSession],
    tenant: DeliveryTenant,
    *,
    state: str = DELIVERY_PENDING,
    attempt: int = 0,
    due_seconds: float = -1.0,
    text: str = FINAL_TEXT,
    entry_id: uuid.UUID | None = ENTRY_ID,
) -> SeededIntent:
    """真实会话 + 终态 Run + 终态事件 + 投递意图行（全部真实物理 FK/逻辑引用）。"""
    run_id, conversation_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    seq = next(_SEQ)
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=1,
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
                user_id=tenant.user_id,
                agent_id=tenant.agent_id,
                status=RunStatus.COMPLETED,
                input_text="seed",
                trace_id=f"pump-seed-{seq}",
                start_time=now,
                deadline_at=now + timedelta(seconds=600),
                channel_json=_channel_json(),
            )
        )
        await session.flush()
        session.add(
            CanonicalEvent(
                id=event_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=1,
                event_type="RUN_COMPLETED",
                stream_type="run.completed",
                payload_json={"status": "COMPLETED", "final_text": text},
            )
        )
        await session.execute(
            pg_insert(ChannelNotificationFeed)
            .values(tenant_id=tenant.tenant_id, user_id=tenant.user_id)
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id"])
        )
        feed_id = await session.scalar(
            sa.select(ChannelNotificationFeed.id).where(
                ChannelNotificationFeed.tenant_id == tenant.tenant_id,
                ChannelNotificationFeed.user_id == tenant.user_id,
            )
        )
        assert feed_id is not None
        row = ChannelNotification(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            feed_id=feed_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=tenant.user_id,
            entry_id=entry_id or uuid.uuid4(),
            kind=KIND_RUN_COMPLETED,
            seq=next(_SEQ),
            delivery_key=run_terminal_delivery_key(run_id),
            delivery_state=state,
            attempt=attempt,
            next_attempt_at=now + timedelta(seconds=due_seconds),
            event_id=event_id,
        )
        session.add(row)
        await session.flush()
    return SeededIntent(
        notification_id=row.id,
        delivery_key=row.delivery_key,
        run_id=run_id,
        conversation_id=conversation_id,
        event_id=event_id,
        tenant_id=tenant.tenant_id,
        user_id=tenant.user_id,
    )


async def _row(notification_id: uuid.UUID) -> dict[str, Any]:
    """独立会话读取真实行事实（断言不依赖泵的返回值）。"""
    async with runtime_db.get_session_factory()() as session:
        result = (
            await session.execute(
                sa.text(
                    "SELECT delivery_state, attempt, lease_owner, lease_epoch, lease_until,"
                    " next_attempt_at, delivery_result, read_at"
                    " FROM runtime.channel_notification WHERE id = :id"
                ).bindparams(id=notification_id)
            )
        ).mappings().one()
    return dict(result)


async def _make_due(notification_id: uuid.UUID) -> None:
    """真实行更新：把退避期限拨到过去（不改实现里的时间判断）。"""
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.channel_notification"
                " SET next_attempt_at = now() - interval '1 second', update_time = now()"
                " WHERE id = :id"
            ).bindparams(id=notification_id)
        )
        await session.commit()


async def _expire_lease(notification_id: uuid.UUID) -> None:
    async with runtime_db.get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.channel_notification"
                " SET lease_until = now() - interval '1 second', update_time = now()"
                " WHERE id = :id"
            ).bindparams(id=notification_id)
        )
        await session.commit()


async def _seconds_from_now(notification_id: uuid.UUID, column: str) -> float:
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


async def _wait_until(predicate: Callable[[], Any], *, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not met within the wait budget")


async def _claim_plan_indexes() -> set[str]:
    """真实执行计划：领取谓词走哪个索引（到期集合的唯一合法入口是 due partial 索引）。"""
    statement = due_intents_statement(
        now=datetime.now(UTC), max_attempts=3, limit=10
    ).compile(dialect=sa.dialects.postgresql.dialect(), compile_kwargs={"literal_binds": True})
    async with runtime_db.get_session_factory()() as session:
        await session.execute(sa.text("SET LOCAL enable_seqscan = off"))
        plan = await session.scalar(
            sa.text(f"EXPLAIN (FORMAT JSON) {str(statement)}")
        )
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("Index Name"), str):
                names.add(node["Index Name"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(plan)
    return names


# --------------------------------------------------------------------------------------
# 1. 到期领取：只领该领的，且走 due partial 索引
# --------------------------------------------------------------------------------------


async def test_b266_due_claim_uses_the_pending_index_and_bounds_attempts(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """到期 PENDING 才被领取；未到期/超预算/网页入箱一律不领；计划走 due 索引。"""
    due = await _seed_intent(delivery_runtime, delivery_tenant)
    future = await _seed_intent(delivery_runtime, delivery_tenant, due_seconds=300)
    exhausted = await _seed_intent(delivery_runtime, delivery_tenant, attempt=3)
    inbox = await _seed_intent(delivery_runtime, delivery_tenant, state=DELIVERY_NOT_REQUIRED)
    pump = pump_builder(gateway_probe, pump_policy())

    claimed = await pump.claim_due(limit=10)

    assert [intent.notification_id for intent in claimed] == [due.notification_id]
    assert claimed[0].delivery_key == due.delivery_key
    assert claimed[0].route is not None
    assert claimed[0].route.model_dump(mode="json") == {
        "channel": "WECOM",
        "bot_id": BOT_ID,
        "external_user_id": EXTERNAL_USER_ID,
        "external_conversation_id": EXTERNAL_THREAD_ID,
    }
    assert claimed[0].text == FINAL_TEXT
    for untouched in (future, exhausted, inbox):
        assert (await _row(untouched.notification_id))["attempt"] == (
            3 if untouched is exhausted else 0
        )
    assert "ix_channel_notification_due" in await _claim_plan_indexes()


# --------------------------------------------------------------------------------------
# 2. 真实交付到原入口：稳定键 + Run 持久路由 + 终态正文
# --------------------------------------------------------------------------------------


async def test_b266_pending_intent_is_delivered_to_the_original_entry_over_real_http(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """PENDING 意图经真实 HTTP 发到原入口；只有实际发送成功才 DELIVERED，已读另记。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    pump = pump_builder(gateway_probe, pump_policy())

    assert await pump.run_once() == 1
    await pump.drain()

    assert gateway_probe.hits == 1
    request = gateway_probe.state.requests[0]
    body = request["body"]
    assert body["delivery_key"] == seed.delivery_key
    assert body["tenant_id"] == delivery_tenant.tenant_id
    assert body["route"] == {
        "channel": "WECOM",
        "bot_id": BOT_ID,
        "external_user_id": EXTERNAL_USER_ID,
        "external_conversation_id": EXTERNAL_THREAD_ID,
    }
    assert body["message"] == {"type": "text", "text": FINAL_TEXT, "artifact": None}
    assert request["headers"]["x-internal-service"] == INTERNAL_TOKEN
    row = await _row(seed.notification_id)
    assert row["delivery_state"] == "DELIVERED"
    assert row["attempt"] == 1 and row["lease_owner"] is None and row["lease_until"] is None
    assert row["delivery_result"]["outcome"] == "DELIVERED"
    assert row["read_at"] is None, "送达不代替已读"


# --------------------------------------------------------------------------------------
# 3. 无空闲投递槽不领取
# --------------------------------------------------------------------------------------


async def test_b266_no_free_delivery_slot_means_no_claim(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """`领取数量≤空闲投递槽`：槽满时第二次 tick 不领取、不改行。"""
    first = await _seed_intent(delivery_runtime, delivery_tenant)
    second = await _seed_intent(delivery_runtime, delivery_tenant)
    gateway_probe.state.mode = "gated"
    gateway_probe.state.hold = asyncio.Event()
    pump = pump_builder(gateway_probe, pump_policy(concurrency=1, timeout_sec=4.0))

    try:
        assert await pump.run_once() == 1
        assert pump.free_slots == 0 and pump.inflight == 1
        await _wait_until(lambda: _probe_hit(gateway_probe))
        assert await pump.run_once() == 0
        assert gateway_probe.hits == 1
        untouched = await _row(second.notification_id)
        assert untouched["delivery_state"] == DELIVERY_PENDING and untouched["attempt"] == 0
    finally:
        gateway_probe.state.hold.set()
        await pump.drain()
    assert (await _row(first.notification_id))["delivery_state"] == "DELIVERED"


async def _probe_hit(probe: GatewayProbe) -> bool:
    return probe.hits >= 1


# --------------------------------------------------------------------------------------
# 4. NOT_SENT：回 PENDING + 退避；到期后按同一稳定键恢复
# --------------------------------------------------------------------------------------


async def test_b266_not_sent_returns_to_pending_with_backoff_then_recovers(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """网关明确拒绝 ⇒ 确定没发出去：回 PENDING 退避，退避到期后同键恢复交付。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    gateway_probe.state.mode = "reject"
    pump = pump_builder(gateway_probe, pump_policy())

    assert await pump.run_once() == 1
    await pump.drain()

    row = await _row(seed.notification_id)
    assert row["delivery_state"] == DELIVERY_PENDING
    assert row["attempt"] == 1 and row["lease_owner"] is None
    assert row["delivery_result"]["outcome"] == "NOT_SENT"
    wait = await _seconds_from_now(seed.notification_id, "next_attempt_at")
    assert BACKOFF_BASE_SEC - 1 <= wait <= BACKOFF_BASE_SEC + 3, "退避基数来自平台设置"

    assert await pump.run_once() == 0, "退避期内不再领取"
    assert gateway_probe.hits == 1

    await _make_due(seed.notification_id)
    gateway_probe.state.mode = "ok"
    assert await pump.run_once() == 1
    await pump.drain()

    assert gateway_probe.hits == 2
    delivered = await _row(seed.notification_id)
    assert delivered["delivery_state"] == "DELIVERED" and delivered["attempt"] == 2


# --------------------------------------------------------------------------------------
# 5. UNKNOWN：保留核实，不自动重发
# --------------------------------------------------------------------------------------


async def test_b266_unknown_outcome_is_retained_and_never_resent(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """没拿到结论 ⇒ UNKNOWN 保留核实：不再进入到期集合、不自动重发。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    gateway_probe.state.mode = "hang"
    pump = pump_builder(gateway_probe, pump_policy(timeout_sec=0.4))

    assert await pump.run_once() == 1
    await pump.drain()

    row = await _row(seed.notification_id)
    assert row["delivery_state"] == "UNKNOWN"
    assert row["delivery_result"]["outcome"] == "UNKNOWN"
    assert row["lease_owner"] is None and row["attempt"] == 1
    assert await pump.claim_due(limit=10) == []
    assert await pump.run_once() == 0
    assert gateway_probe.hits == 1


# --------------------------------------------------------------------------------------
# 6. 慢发送期间真实续租
# --------------------------------------------------------------------------------------


async def test_b266_lease_is_renewed_while_a_slow_delivery_is_in_flight(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """慢发送期间租约被真实续租（行内 `lease_until` 前进），收尾后清租约并交付。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    gateway_probe.state.mode = "gated"
    gateway_probe.state.hold = asyncio.Event()
    pump = pump_builder(gateway_probe, pump_policy(lease_sec=5, timeout_sec=4.0))

    try:
        assert await pump.run_once() == 1
        await _wait_until(lambda: _state_is(seed, "DELIVERING"))
        first = await _row(seed.notification_id)
        assert first["lease_until"] is not None

        async def _renewed() -> bool:
            current = (await _row(seed.notification_id))["lease_until"]
            return current is not None and current > first["lease_until"]

        await _wait_until(_renewed)
    finally:
        gateway_probe.state.hold.set()
        await pump.drain()

    row = await _row(seed.notification_id)
    assert row["delivery_state"] == "DELIVERED" and row["lease_until"] is None


async def _state_is(seed: SeededIntent, state: str) -> bool:
    return (await _row(seed.notification_id))["delivery_state"] == state


# --------------------------------------------------------------------------------------
# 7. 失约即停手
# --------------------------------------------------------------------------------------


async def test_b266_lease_loss_stops_the_local_delivery_without_writing_any_result(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """租约被接管 ⇒ 旧 attempt 停手：不 ack、不标送达，行仍归新租约。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    gateway_probe.state.mode = "gated"
    gateway_probe.state.hold = asyncio.Event()
    pump = pump_builder(gateway_probe, pump_policy(lease_sec=5, timeout_sec=4.0))
    protocol = TenantChannelDelivery(runtime_db.get_session_factory, DeliverySettings())

    try:
        assert await pump.run_once() == 1
        await _wait_until(lambda: _state_is(seed, "DELIVERING"))
        await _expire_lease(seed.notification_id)
        other = uuid.uuid4()
        takeover = await protocol.reserve(
            tenant_id=seed.tenant_id, delivery_key=seed.delivery_key, attempt_id=other
        )
        assert takeover.lease_epoch == 2 and takeover.attempt == 2
        await _wait_until(lambda: _pump_stopped(pump))
    finally:
        gateway_probe.state.hold.set()
        await pump.drain()

    row = await _row(seed.notification_id)
    assert row["lease_owner"] == str(other) and row["lease_epoch"] == 2
    assert row["delivery_state"] == "DELIVERING"
    assert row["delivery_result"] is None, "被接管的旧 attempt 不写任何投递结果"


async def _pump_stopped(pump: ChannelDeliveryPump) -> Any:
    return pump.inflight == 0


# --------------------------------------------------------------------------------------
# 8. 限次：尝试预算耗尽后不再领取
# --------------------------------------------------------------------------------------


async def test_b266_attempt_budget_bounds_the_claim_set(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """`max_attempts` 用尽 ⇒ 不再领取、不再发送；行保留可核实的 PENDING 状态。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    gateway_probe.state.mode = "reject"
    pump = pump_builder(gateway_probe, pump_policy(max_attempts=2))

    for _ in range(2):
        await _make_due(seed.notification_id)
        assert await pump.run_once() == 1
        await pump.drain()

    assert gateway_probe.hits == 2
    row = await _row(seed.notification_id)
    assert row["delivery_state"] == DELIVERY_PENDING and row["attempt"] == 2
    assert await pump.claim_due(limit=10) == []
    assert await pump.run_once() == 0
    assert gateway_probe.hits == 2


# --------------------------------------------------------------------------------------
# 9. Gateway 重启恢复交付 + 执行槽耗尽也可投递
# --------------------------------------------------------------------------------------


async def test_b266_recovers_after_gateway_restart_with_execution_slots_exhausted(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """网关下线（连接拒绝）后按持久意图恢复交付；执行槽全忙也不挡交付、不被占用。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    supervisor = ExecutionSupervisor(max_concurrency=1)
    gate = asyncio.Event()
    pump = pump_builder(gateway_probe, pump_policy())
    try:
        assert await supervisor.submit(uuid.uuid4(), gate.wait) is True
        assert supervisor.free_slots == 0
        await gateway_probe.stop()

        assert await pump.run_once() == 1
        await pump.drain()
        down = await _row(seed.notification_id)
        assert down["delivery_state"] == DELIVERY_PENDING
        assert down["delivery_result"]["outcome"] == "NOT_SENT", "连接没建立 ⇒ 确定没发出去"
        assert supervisor.execution_count == 1, "交付不占执行槽"

        await gateway_probe.start()
        await _make_due(seed.notification_id)
        assert await pump.run_once() == 1
        await pump.drain()

        assert supervisor.execution_count == 1
        assert (await _row(seed.notification_id))["delivery_state"] == "DELIVERED"
        assert gateway_probe.hits == 1
    finally:
        gate.set()
        await supervisor.close()


# --------------------------------------------------------------------------------------
# 10. 装配根：lifespan 注入启动预算并起独立循环
# --------------------------------------------------------------------------------------


async def test_b266_lifespan_mounts_the_pump_with_startup_budgets(
    delivery_runtime: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """装配根挂载独立投递泵（第四单元内，不新增部署单元），退出即停。"""
    monkeypatch.setenv("CHANNEL_DELIVERY_CONCURRENCY", "3")
    monkeypatch.setenv("CHANNEL_DELIVERY_TIMEOUT_SEC", "3")
    monkeypatch.setenv("CHANNEL_DELIVERY_LEASE_SEC", "20")
    monkeypatch.setenv("CHANNEL_DELIVERY_POLL_SEC", "0.5")
    from muad_agent_runtime import main

    async with main.lifespan(main.app):
        pump = main.app.state.delivery_pump
        assert isinstance(pump, ChannelDeliveryPump)
        assert pump.policy.concurrency == 3 and pump.policy.lease_sec == 20
        assert pump.policy.timeout_sec == 3 and pump.policy.poll_sec == 0.5
        task = main.app.state.delivery_pump_task
        assert not task.done()
    assert task.done()


# --------------------------------------------------------------------------------------
# 11. 边界：启动预算校验与默认值
# --------------------------------------------------------------------------------------


def test_b266_pump_policy_rejects_invalid_budgets() -> None:
    for invalid in (
        {"concurrency": 0},
        {"batch_size": 0},
        {"max_attempts": 0},
        {"timeout_sec": 0.0},
        {"lease_sec": 4},
        {"timeout_sec": 30.0},
        {"retry_base_sec": 0.0},
        {"retry_base_sec": 5.0, "retry_cap_sec": 1.0},
    ):
        with pytest.raises(ValueError):
            pump_policy(**invalid)


def test_b266_pump_policy_derives_from_the_startup_settings() -> None:
    policy = DeliveryPumpPolicy.from_settings(SharedSettings())
    assert policy.concurrency == 4 and policy.batch_size == 32
    assert policy.lease_sec == 30 and policy.timeout_sec == 5.0
    assert policy.retry_base_sec == 1.0 and policy.retry_cap_sec == 30.0
    assert policy.max_attempts == 20 and policy.poll_sec == 1.0


# --------------------------------------------------------------------------------------
# 12. 与 TASK-063 写入路径衔接：同事务写下的 PENDING 意图被同一稳定键交付
# --------------------------------------------------------------------------------------


async def test_b266_intent_written_by_the_notification_path_is_delivered(
    delivery_runtime: async_sessionmaker[AsyncSession],
    delivery_tenant: DeliveryTenant,
    pump_builder: Callable[..., ChannelDeliveryPump],
    gateway_probe: GatewayProbe,
) -> None:
    """真实通知写入路径（TASK-063）产出的 PENDING 意图由本泵领取并交付。"""
    seed = await _seed_intent(delivery_runtime, delivery_tenant)
    async with delivery_runtime() as session:
        await session.execute(
            sa.text("DELETE FROM runtime.channel_notification WHERE id = :id").bindparams(
                id=seed.notification_id
            )
        )
        run = await session.get(RunRecord, seed.run_id)
        assert run is not None
        receipt = await write_run_notification(
            session,
            run=run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=seed.delivery_key,
            delivery_state=DELIVERY_PENDING,
            event_id=seed.event_id,
        )
        await session.commit()
    assert receipt is not None and receipt.created is True
    pump = pump_builder(gateway_probe, pump_policy())

    assert await pump.run_once() == 1
    await pump.drain()

    assert gateway_probe.state.requests[0]["body"]["delivery_key"] == seed.delivery_key
    assert (await _row(receipt.id))["delivery_state"] == "DELIVERED"
