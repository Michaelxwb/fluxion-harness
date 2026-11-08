"""B-267 / TASK-067：机器人收尾与主动兜底统一去重（design §3.6「机器人终态交付」）。

真实边界（业务路径不 mock）：

- **真实 Gateway 交付管线与适配器**：真实 `InboundPipeline`（受理→SSE→终态交付）+ 真实
  `WeComAdapter`（官方 SDK 端口为进程内探针，与 TASK-048 同一装配口径）+ 真实
  `RuntimeClient`/`ConsoleClient` 走真实 HTTP；
- **真实 HTTP/SSE 协议探针**：Runtime 侧是真实 TCP 服务上的真实 API-18 路由
  （`/internal/channel-notifications/reserve|ack`，真实 `require_internal_service` 门控与真实
  PG 状态机）与真实 `text/event-stream` 应答；Console 侧是真实 HTTP 桩；
- **数据库经 Owner 接口访问，禁止 Gateway 持库**：本文件不向 Gateway 注入任何数据库对象，
  Gateway 只做 HTTP 调用；测试自己用独立连接读行事实（Owner 视角）；
- **真实 Runtime 投递泵**（`ChannelDeliveryPump` + `TenantChannelDelivery`）作为「主动兜底」，
  按同一稳定键领取/确认。

关键断言（B-267 契约）：

1. **活跃 ReplySession 收尾与主动兜底使用同一稳定 delivery_key**
   （`run:{run_id}:terminal`）**预留/确认**：兜底主动交付先按该键 reserve、实际发送成功才 ack；
   确认落库后兜底泵的同键领取为空、不再重发；
2. **已送达回执重放**：兜底先送达时，活跃会话收尾的同键认领命中冻结的首次回执，不重发、
   不重写回执；
3. **路由保持原 entry/Agent/user 并重核接收权限**：交付路由仍是原 bot/用户/会话（企微按原
   入口指针），交付前经 Console 复核接收者与 Agent 未变且仍被授权；复核不通过即不发送、
   不确认（行仍是 PENDING）；
4. **ACK 丢失保留可追溯至少一次语义**：文本已发出但确认未落库 ⇒ 行留在 DELIVERING
   （attempt/租约可追溯、`delivery_result` 仍为空、不把占位标送达）；租约到期后同键重来被
   接管（epoch 前进）并再次投递 ⇒ 至少一次。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fakes import FakeWeComSdkFactory, StubConsole
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from muad_agent_runtime.api.channel_delivery import (
    ChannelDeliveryPort,
    get_channel_delivery_port,
)
from muad_agent_runtime.api.channel_delivery import (
    router as channel_delivery_router,
)
from muad_agent_runtime.application.channel_delivery import TenantChannelDelivery
from muad_agent_runtime.application.channel_delivery_pump import (
    ChannelDeliveryPump,
    DeliveryIntent,
    DeliveryPumpPolicy,
    DeliverySendOutcome,
)
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_PENDING,
    KIND_RUN_COMPLETED,
    run_terminal_delivery_key,
    write_run_notification,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from muad_api import AppError, install_api_foundation
from muad_api.catalog import MessageCatalog
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    BotSnapshotItem,
    ChannelContext,
    ChannelEnvelope,
    RunStatus,
)
from muad_contracts.channel_delivery import (
    DeliveryAck,
    DeliveryAckReceipt,
    DeliveryLeaseGrant,
    DeliveryLeaseRenewal,
    DeliveryReservationRequest,
)
from muad_im_connector.channels.wecom.adapter import WeComAdapter
from muad_im_connector.channels.wecom.sdk_port import WeComInboundMessage
from muad_im_gateway.application.channel_delivery import (
    ActiveChannelDelivery,
    ConsoleDeliveryPermission,
)
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.acceptance.runtime.conftest import _Server

pytestmark = pytest.mark.integration

TENANT = "tenant-b267"
BOT_ID = "wecom-bot-b267"
WECOM_SECRET = "b267-wecom-secret"
CHAT = "chat-b267"
SERVICE_TOKEN = "b267-internal-service-token"
ENTRY_ID = uuid.UUID("4f8d2c31-6a5e-4b90-8c47-1d2e3f4a5b60")
AGENT_ID = uuid.UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
PLATFORM_USER_ID = uuid.UUID("9f4a1c22-6d3b-4c8e-9a10-2f5b7d8e0a11")
RUN_ID = uuid.UUID("b2670000-1111-4222-8333-444455556666")
FINAL_TEXT = "完整答案：设备检查完成"
INPUT_TEXT = "检查设备"
MESSAGE_ID = "msg-b267-1"
REPLY_ID = "req-b267-1"
RUNS_PATH = "/v1/runs"
RESOLVE_PATH = "/internal/channel/resolve"
ACK_PATH = "/internal/channel-notifications/{notification_id}/ack"
WAIT_TIMEOUT_SEC = 10.0
_LEASE_SEC = 30


# --------------------------------------------------------------------------------------
# 隔离 PostgreSQL：测试侧独立连接（Gateway 不持库，DB 只经 Owner 接口访问）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Store:
    database_url: str
    factory: async_sessionmaker[AsyncSession]

    def sessions(self) -> async_sessionmaker[AsyncSession]:
        """`SessionFactoryProvider`：泵/协议门面与测试读行共用同一条连接口径。"""
        return self.factory


@pytest.fixture
async def store(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[_Store]:
    """隔离 PostgreSQL 号位 + 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", "http://127.0.0.1:9")
    engine = create_async_engine(async_tool_datastore["database_url"], pool_pre_ping=True)
    try:
        yield _Store(
            database_url=async_tool_datastore["database_url"],
            factory=async_sessionmaker(engine, expire_on_commit=False),
        )
    finally:
        await engine.dispose()


class _LoopSessions:
    """服务线程有自己的事件循环：engine 必须由那个循环拥有（不能借测试循环的 engine）。"""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._factories: dict[int, async_sessionmaker[AsyncSession]] = {}

    def __call__(self) -> async_sessionmaker[AsyncSession]:
        loop = asyncio.get_running_loop()
        factory = self._factories.get(id(loop))
        if factory is None:
            engine = create_async_engine(self._database_url, pool_pre_ping=True)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            self._factories[id(loop)] = factory
        return factory


class _DeliverySettings:
    """平台设置源（Console 权威）：退避基数与回执重放保留期由它裁决。"""

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "task": {"delivery_backoff_base_sec": 5},
                "webchat": {"notification_retention_days": 30},
            },
        )


# --------------------------------------------------------------------------------------
# 真实 Runtime HTTP/SSE 探针：真实 API-18 路由 + 真实 PG 状态机
# --------------------------------------------------------------------------------------


def _frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _RecordingPort:
    """真实 API-18 端口的记录包装：请求走**真实状态机**，同时留下稳定键/结局的观测。"""

    def __init__(self, inner: ChannelDeliveryPort, *, drop_first_ack: bool = False) -> None:
        self._inner = inner
        self._drop_first_ack = drop_first_ack
        self.reserves: list[DeliveryReservationRequest] = []
        self.acks: list[DeliveryAck] = []
        self.ack_calls = 0

    async def reserve(
        self, *, tenant_id: str, body: DeliveryReservationRequest
    ) -> DeliveryLeaseGrant:
        self.reserves.append(body)
        return await self._inner.reserve(tenant_id=tenant_id, body=body)

    async def renew(
        self, *, tenant_id: str, notification_id: uuid.UUID, body: DeliveryLeaseRenewal
    ) -> DeliveryLeaseGrant:
        return await self._inner.renew(tenant_id=tenant_id, notification_id=notification_id, body=body)

    async def ack(
        self, *, tenant_id: str, notification_id: uuid.UUID, body: DeliveryAck
    ) -> DeliveryAckReceipt:
        self.ack_calls += 1
        if self._drop_first_ack and self.ack_calls == 1:
            # 确认响应丢失：发送成功但确认没有落库（真实错误封套由异常处理器给出）。
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        self.acks.append(body)
        return await self._inner.ack(
            tenant_id=tenant_id, notification_id=notification_id, body=body
        )


class _RuntimeProbe:
    """真实 Runtime HTTP 探针：API-18 真路由（真实 PG）+ 真实 SSE 应答 + 故障注入。"""

    def __init__(self, database_url: str, *, drop_first_ack: bool = False) -> None:
        app = FastAPI()
        install_api_foundation(app)
        self.port = _RecordingPort(
            ChannelDeliveryPort(_LoopSessions(database_url), _DeliverySettings()),
            drop_first_ack=drop_first_ack,
        )
        self.run_requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

        app.include_router(channel_delivery_router)
        app.dependency_overrides[get_channel_delivery_port] = lambda: self.port

        @app.post(RUNS_PATH)
        async def _runs(request: Request) -> StreamingResponse:
            body = json.loads(await request.body() or b"{}")
            with self._lock:
                self.run_requests.append(body)

            async def _stream() -> AsyncIterator[str]:
                yield _frame("run.created", {"run_id": str(RUN_ID), "resumed": False})
                yield _frame("message.delta", {"delta": "检查中…"})
                yield _frame("run.completed", {"status": "COMPLETED", "final_text": FINAL_TEXT})

            return StreamingResponse(_stream(), media_type="text/event-stream")

        self._server = _Server(app)
        self.url = ""

    def start(self) -> None:
        self.url = self._server.start()

    def stop(self) -> None:
        self._server.stop()


@pytest.fixture()
def runtime_probe(store: _Store) -> Iterator[Callable[..., _RuntimeProbe]]:
    probes: list[_RuntimeProbe] = []

    def build(*, drop_first_ack: bool = False) -> _RuntimeProbe:
        probe = _RuntimeProbe(store.database_url, drop_first_ack=drop_first_ack)
        probe.start()
        probes.append(probe)
        return probe

    try:
        yield build
    finally:
        for probe in probes:
            probe.stop()


@pytest.fixture()
def stub_console() -> Iterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


def _console_resolve(
    stub: StubConsole,
    *,
    authorized: Callable[[int], bool] = lambda _call: True,
    agent_id: uuid.UUID = AGENT_ID,
    platform_user_id: uuid.UUID = PLATFORM_USER_ID,
) -> list[dict[str, Any]]:
    """真实 Console HTTP 桩：按调用序返回裁决（受理时与交付时可不同）。"""
    bodies: list[dict[str, Any]] = []

    async def resolve(request: Request) -> Response:
        bodies.append(json.loads(await request.body() or b"{}"))
        return JSONResponse(
            status_code=200,
            content={
                "code": "0",
                "msg": "stub",
                "data": {
                    "bound": True,
                    "entry_id": str(ENTRY_ID),
                    "authorized": authorized(len(bodies)),
                    "agent_id": str(agent_id),
                    "platform_user_id": str(platform_user_id),
                },
            },
        )

    stub.handlers[RESOLVE_PATH] = resolve
    return bodies


# --------------------------------------------------------------------------------------
# 真实行种子与读取（只写/读本用例的隔离库）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Seeded:
    notification_id: uuid.UUID
    conversation_id: uuid.UUID
    delivery_key: str


def _channel_json() -> dict[str, Any]:
    """Run 持久化的原入口路由（主动兜底按它交付，不重新猜来源）。"""
    return ChannelContext(
        type="WECOM",
        entry_id=ENTRY_ID,
        bot_id=BOT_ID,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id=CHAT,
    ).model_dump(mode="json")


async def _seed_intent(store: _Store) -> _Seeded:
    """真实会话 + 终态 Run + 终态事件 + **真实通知接缝**写下的 PENDING 出站意图。"""
    now = datetime.now(UTC)
    conversation_id = uuid.uuid4()
    event_id = uuid.uuid4()
    async with store.factory() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=TENANT,
                user_id=PLATFORM_USER_ID,
                agent_id=AGENT_ID,
                status="ACTIVE",
                last_seq=1,
                channel_account_id=ENTRY_ID,
                last_message_at=now,
            )
        )
        await session.flush()
        run = RunRecord(
            id=RUN_ID,
            tenant_id=TENANT,
            conversation_id=conversation_id,
            user_id=PLATFORM_USER_ID,
            agent_id=AGENT_ID,
            status=RunStatus.COMPLETED,
            input_text=INPUT_TEXT,
            input_message_id=MESSAGE_ID,
            trace_id="b267-seed",
            start_time=now,
            deadline_at=now + timedelta(seconds=600),
            channel_json=_channel_json(),
        )
        session.add(run)
        await session.flush()
        session.add(
            CanonicalEvent(
                id=event_id,
                tenant_id=TENANT,
                conversation_id=conversation_id,
                run_id=RUN_ID,
                seq=1,
                event_type="RUN_COMPLETED",
                stream_type="run.completed",
                payload_json={"status": "COMPLETED", "final_text": FINAL_TEXT},
            )
        )
        receipt = await write_run_notification(
            session,
            run=run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=run_terminal_delivery_key(RUN_ID),
            event_id=event_id,
            delivery_state=DELIVERY_PENDING,
        )
        assert receipt is not None, "会话有入口归属：出站意图必须落库"
        notification_id = receipt.id
    return _Seeded(
        notification_id=notification_id,
        conversation_id=conversation_id,
        delivery_key=run_terminal_delivery_key(RUN_ID),
    )


async def _row(store: _Store, notification_id: uuid.UUID) -> dict[str, Any]:
    """独立连接读取真实行事实（断言不依赖被测代码的返回值）。"""
    async with store.factory() as session:
        result = (
            await session.execute(
                sa.text(
                    "SELECT delivery_key, delivery_state, attempt, lease_owner, lease_epoch,"
                    " lease_until, delivery_result, read_at"
                    " FROM runtime.channel_notification WHERE id = :id"
                ).bindparams(id=notification_id)
            )
        ).mappings().one()
    return dict(result)


async def _expire_lease(store: _Store, notification_id: uuid.UUID) -> None:
    """真实行更新：把租约期限拨到过去（不改实现里的时间判断）。"""
    async with store.factory() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.channel_notification"
                " SET lease_until = now() - interval '1 second', update_time = now()"
                " WHERE id = :id"
            ).bindparams(id=notification_id)
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# 真实 Gateway 交付管线（企微适配器 + 真实 HTTP 出口）
# --------------------------------------------------------------------------------------


class _RecordingSender:
    """主动兜底的发送探针：记录渠道中立的交付请求（Runtime→Gateway HTTP 出站面属 TASK-066）。"""

    def __init__(self) -> None:
        self.intents: list[DeliveryIntent] = []

    async def send(
        self, intent: DeliveryIntent, *, attempt_id: Any, lease_epoch: int
    ) -> DeliverySendOutcome:
        self.intents.append(intent)
        return DeliverySendOutcome(outcome="DELIVERED", result={"delivered": True})


def _pump_policy() -> DeliveryPumpPolicy:
    return DeliveryPumpPolicy(
        concurrency=2,
        timeout_sec=1.0,
        lease_sec=5,
        poll_sec=0.05,
        batch_size=8,
        retry_base_sec=0.05,
        retry_cap_sec=0.2,
        max_attempts=3,
    )


@asynccontextmanager
async def _proactive_pump(store: _Store, sender: _RecordingSender) -> AsyncIterator[ChannelDeliveryPump]:
    """真实 Runtime 投递泵（真实 reserve/ack 状态转换 + 记录式发送）。"""
    pump = ChannelDeliveryPump(
        store.sessions,
        TenantChannelDelivery(store.sessions, _DeliverySettings()),
        sender,
        _pump_policy(),
    )
    try:
        yield pump
    finally:
        await pump.close()


@dataclass(slots=True)
class _Gateway:
    adapter: WeComAdapter
    pipeline: InboundPipeline
    sdk: Any


@asynccontextmanager
async def _gateway_stack(
    catalog: MessageCatalog,
    *,
    console_url: str,
    runtime_url: str,
) -> AsyncIterator[_Gateway]:
    """真实企微适配器 + 真实交付协调器（API-18 走真实 HTTP）+ 真实入站管线。"""
    sdk_factory = FakeWeComSdkFactory()
    adapter = WeComAdapter(
        sdk_factory=sdk_factory,
        bots=[
            BotSnapshotItem(
                bot_account_id=ENTRY_ID,
                bot_id=BOT_ID,
                secret=WECOM_SECRET,
                agent_id=AGENT_ID,
            )
        ],
        backoff_base_sec=0.01,
        backoff_max_sec=0.02,
        stream_flush_interval_sec=1000.0,
    )
    await adapter.start()
    console = ConsoleClient(console_url)
    runtime = RuntimeClient(runtime_url)
    delivery = ActiveChannelDelivery(
        runtime,
        permission=ConsoleDeliveryPermission(console),
        lease_sec=_LEASE_SEC,
    )
    pipeline = InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=TENANT,
        delta_flush_interval_sec=0.0,
        channel_delivery=delivery,
        runtime_reconnect_attempts=0,
    )
    try:
        yield _Gateway(adapter=adapter, pipeline=pipeline, sdk=sdk_factory.latest())
    finally:
        await adapter.stop()
        await console.aclose()
        await runtime.aclose()


def _envelope() -> ChannelEnvelope:
    """原入口的信封：bot（entry）+ 用户 + 会话；回复会话按 message_id 找回自己的回调。"""
    return ChannelEnvelope(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id=CHAT,
        message_id=MESSAGE_ID,
        text=INPUT_TEXT,
    )


def _live_envelope(adapter: WeComAdapter, sdk: Any) -> ChannelEnvelope:
    """经真实 SDK 端口推入一条入站消息（登记了本条消息的回调 ⇒ 回复会话可打开）。"""
    sdk.push_message(
        WeComInboundMessage(
            bot_id=BOT_ID,
            message_id=MESSAGE_ID,
            external_user_id=str(PLATFORM_USER_ID),
            external_conversation_id=CHAT,
            message_type="text",
            text=INPUT_TEXT,
            reply_id=REPLY_ID,
        )
    )
    return _envelope()


async def _wait_until(predicate: Callable[[], bool], *, what: str) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(what)
        await asyncio.sleep(0.02)


# --------------------------------------------------------------------------------------
# 1. 兜底主动交付：同键预留/确认，兜底泵随后不再重发
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# 终态单发送者：活跃会话只做增量展示，终态正文只由持久投递泵发送
# --------------------------------------------------------------------------------------


async def test_b267_terminal_is_sent_only_by_the_persistent_pump(
    store: _Store,
    stub_console: StubConsole,
    runtime_probe: Callable[..., _RuntimeProbe],
    catalog: MessageCatalog,
) -> None:
    """Run 终态：活跃 ReplySession 不再发送正文/收尾/主动兜底；持久投递泵是唯一发送者。"""
    seeded = await _seed_intent(store)
    _console_resolve(stub_console)
    probe = runtime_probe()
    sender = _RecordingSender()
    async with _proactive_pump(store, sender) as pump:
        async with _gateway_stack(
            catalog, console_url=stub_console.url, runtime_url=probe.url
        ) as gateway:
            envelope = _live_envelope(gateway.adapter, gateway.sdk)
            assert await gateway.pipeline.handle(gateway.adapter, envelope) is True
            # 活跃会话**不**发送终态正文、**不**收尾、**不**占用投递协调端口。
            assert gateway.sdk.sent_texts == []
            assert probe.port.reserves == [] and probe.port.acks == []
            row = await _row(store, seeded.notification_id)
            assert row["delivery_state"] == "PENDING" and row["attempt"] == 0

        # 持久投递泵按稳定键发送：携带原消息身份、终态状态与受理身份。
        assert await pump.run_once() == 1
        await pump.drain()
        assert [intent.delivery_key for intent in sender.intents] == [seeded.delivery_key]
        intent = sender.intents[0]
        assert intent.message_id == MESSAGE_ID, "终态必须带回原消息身份以收尾原回复流"
        assert intent.status == "COMPLETED"
        assert intent.agent_id == AGENT_ID and intent.platform_user_id == PLATFORM_USER_ID
        assert intent.route is not None and intent.route.bot_id == BOT_ID
        assert intent.text == FINAL_TEXT

    delivered = await _row(store, seeded.notification_id)
    assert delivered["delivery_state"] == "DELIVERED"
    assert delivered["delivery_result"] is not None


async def test_b267_pump_delivery_is_not_repeated_by_the_gateway(
    store: _Store,
    stub_console: StubConsole,
    runtime_probe: Callable[..., _RuntimeProbe],
    catalog: MessageCatalog,
) -> None:
    """泵先送达后，活跃会话不得再次发送/占键：首次回执保持不变。"""
    seeded = await _seed_intent(store)
    _console_resolve(stub_console)
    probe = runtime_probe()
    sender = _RecordingSender()
    async with _proactive_pump(store, sender) as pump:
        assert await pump.run_once() == 1
        await pump.drain()
        first = await _row(store, seeded.notification_id)
        assert first["delivery_state"] == "DELIVERED"
        receipt = first["delivery_result"]
        reserves_before = list(probe.port.reserves)

        async with _gateway_stack(
            catalog, console_url=stub_console.url, runtime_url=probe.url
        ) as gateway:
            envelope = _live_envelope(gateway.adapter, gateway.sdk)
            assert await gateway.pipeline.handle(gateway.adapter, envelope) is True

        assert gateway.sdk.sent_texts == [], "已由持久链送达，会话路径不得重发"
        assert probe.port.reserves == reserves_before, "活跃路径不得再占投递键"

    after = await _row(store, seeded.notification_id)
    assert after["delivery_state"] == "DELIVERED"
    assert after["delivery_result"] == receipt, "首次结果不被改写"
