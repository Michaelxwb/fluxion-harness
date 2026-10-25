"""B-247 Gateway 注册 WEBCHAT 标准通道适配器（design §3.2 / §3.3；TASK-047 + TASK-010）。

真实边界（业务路径不 mock）：

- **真实通道装配与快照分发**：真实 `build_channel_registry`（装配根同形）、真实
  `BotSnapshotCache` 与真实 `dispatch_snapshot`；Console 是真实 HTTP 桩；企微侧用真实
  `WeComAdapter` + `FakeWeComSdkFactory` 记录它是否**真的**去建客户端/读 secret。
- **真实出站订阅面**：装配注入的 `WebchatDeliverySink` 收到渠道中立载荷；未装配即显式失败。
- 网关不持库：本文件不碰任何数据库（DB 只经 Owner 接口访问，网关只做 HTTP 调用）。

TASK-010 起 WEBCHAT 入站是 **SYNC**（HTTP → 统一消息核心 → Runtime）：适配器不再有进程内
入站队列/消费循环，`iter_events()` 恒为空；HTTP 受理的端到端行为由
`tests/acceptance/im_gateway/test_webchat_core.py`（S-08）用真实栈覆盖。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fakes import FakeWeComSdkFactory, StubConsole
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from muad_common import SharedSettings
from muad_contracts import BotSnapshotItem, DeliveryMessage, DeliveryRouteInput
from muad_im_connector.channels.wecom.adapter import WeComAdapter
from muad_im_core import BotSnapshotCache
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.channels.base import ChannelAdapter, ChannelAdapterUnavailable, ChannelRegistry
from muad_im_gateway.channels.registry import build_channel_registry, dispatch_snapshot
from muad_im_gateway.channels.webchat import WebchatChannelAdapter
from muad_im_gateway.channels.webhook import WebhookChannelAdapter

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-b247"
ENTRY_ID = "entry-b247"
PLATFORM_USER_ID = uuid.UUID("9f4a1c22-6d3b-4c8e-9a10-2f5b7d8e0a11")
CONVERSATION_ID = uuid.UUID("3c7d5e91-0b24-4a6f-8d33-5e9a1c4b7f20")
AGENT_ID = uuid.UUID("a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")
BOTS_PATH = "/internal/channel/bots"
#: 企微凭据 canary：只允许出现在发给企微 SDK 的 secret 路径上，不得因 WEBCHAT 条目被触碰。
WECOM_SECRET_CANARY = "b247-wecom-secret-canary"


def _envelope(data: Any) -> JSONResponse:
    return JSONResponse(status_code=200, content={"code": "0", "msg": "stub", "data": data})


class _OwnerProbe:
    """Console Owner 接口的真实 HTTP 桩：只回答入口快照（本文件不需要其它 Owner 端点）。"""

    def __init__(self, stub: StubConsole) -> None:
        self.snapshot_items: tuple[BotSnapshotItem, ...] = ()

        async def bots(_: Request) -> Response:
            items = [item.model_dump(mode="json") for item in self.snapshot_items]
            return _envelope(
                {
                    "revision": "sha256:" + "0" * 64,
                    "items": items,
                    "page": 1,
                    "page_size": 20,
                    "total": len(items),
                }
            )

        stub.handlers[BOTS_PATH] = bots


class _RecordingSink:
    """出站订阅面探针（TASK-052/061 的 SSE 投影面同形）：只记录渠道中立的投递。"""

    def __init__(self) -> None:
        self.delivered: list[tuple[DeliveryRouteInput, DeliveryMessage]] = []
        self.streamed: list[tuple[DeliveryRouteInput, tuple[str, ...]]] = []

    async def deliver(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        self.delivered.append((route, message))

    async def deliver_stream(
        self, route: DeliveryRouteInput, chunks: AsyncIterator[str]
    ) -> None:
        self.streamed.append((route, tuple([chunk async for chunk in chunks])))


@pytest.fixture()
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


def _route() -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WEBCHAT",
        bot_id=ENTRY_ID,
        external_user_id=str(PLATFORM_USER_ID),
        external_conversation_id=str(CONVERSATION_ID),
    )


async def _chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


async def _iter_events(adapter: WebchatChannelAdapter) -> list[Any]:
    events = await adapter.iter_events()
    return [event async for event in events]


# --- B-247 ------------------------------------------------------------------------------


async def test_b247_snapshot_dispatch_by_channel_never_touches_wecom_secret_path(
    stub_console: StubConsole,
) -> None:
    """快照按 `BotSnapshotItem.channel` 分发：WEBCHAT 条目不进企微的长连接/secret 路径。"""
    owner = _OwnerProbe(stub_console)
    webchat_item = BotSnapshotItem(
        bot_account_id=uuid.uuid4(),
        bot_id=ENTRY_ID,
        channel="WEBCHAT",
        secret=None,
        agent_id=AGENT_ID,
    )
    wecom_item = BotSnapshotItem(
        bot_account_id=uuid.uuid4(),
        bot_id="wecom-bot-b247",
        channel="WECOM",
        secret=WECOM_SECRET_CANARY,
        agent_id=AGENT_ID,
    )
    owner.snapshot_items = (webchat_item, wecom_item)

    factory = FakeWeComSdkFactory()
    wecom = WeComAdapter(sdk_factory=factory)
    webchat = WebchatChannelAdapter()
    registry = ChannelRegistry()
    registry.register(wecom)
    registry.register(webchat)
    await registry.start_all()

    console = ConsoleClient(stub_console.url)
    cache = BotSnapshotCache(
        console,
        tenant_id=GATEWAY_TENANT,
        on_snapshot_changed=dispatch_snapshot(registry),
    )
    try:
        await cache.refresh()
    finally:
        await console.aclose()

    assert cache.revision is not None, "真实 Console HTTP 的快照必须被完整发布"
    # 每个适配器只拿到**自己通道**的条目；WEBCHAT 是稳定入口，无需 secret
    assert webchat.enabled_entries == (webchat_item,)
    assert webchat.enabled_entries[0].secret is None
    # 只有企微 bot 走了 secret/长连接路径：WEBCHAT 条目**从未**被交给企微 SDK
    assert factory.secrets == [("wecom-bot-b247", WECOM_SECRET_CANARY)]
    assert [client.bot_id for client in factory.clients] == ["wecom-bot-b247"]
    assert wecom.connection_states == {"wecom-bot-b247": "CONNECTED"}

    # 企微适配器自己按实际 channel 过滤：整份快照直接给它，WEBCHAT 条目也不开连接
    await wecom.apply_snapshot((webchat_item,))
    assert [client.bot_id for client in factory.clients] == ["wecom-bot-b247"]
    assert wecom.connection_states == {}
    await registry.stop_all()


async def test_b247_registry_registers_webchat_standard_adapter_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装配根同形构建：WEBCHAT + 配置驱动的通用 webhook 通道，均无需 bot secret 即可用。"""
    monkeypatch.setenv(
        "GATEWAY_WEBHOOK_CHANNELS",
        '[{"channel": "WECOM", "secret": "test-webhook-secret", "reply_url": null}]',
    )
    registry = build_channel_registry(SharedSettings())
    assert registry.names() == ("WEBCHAT", "WECOM")
    webhook = registry.get("WECOM")
    assert isinstance(webhook, WebhookChannelAdapter)
    adapter = registry.get("WEBCHAT")
    assert isinstance(adapter, WebchatChannelAdapter)
    standard: ChannelAdapter = adapter
    assert standard.name == "WEBCHAT"
    # 统一消息核心的通道身份与注册名同源（SYNC 受理只用这一个判断）。
    assert adapter.channel == "WEBCHAT"

    await adapter.start()
    # 无 bot、无 secret：start() 不依赖任何凭据，入口快照为空。
    assert adapter.enabled_entries == ()
    # TASK-010：WEBCHAT 入站是 SYNC，没有进程内队列 —— `iter_events()` 恒空、无队列属性。
    assert await _iter_events(adapter) == []
    for attribute in ("submit", "pending", "max_pending"):
        assert not hasattr(adapter, attribute), attribute
    await adapter.stop()


async def test_b247_outbound_maps_channel_neutral_payload_and_fails_explicitly_when_unwired() -> None:
    """出站：装配注入的订阅面端口收到渠道中立载荷；未装配则显式失败，不静默丢。"""
    route = _route()
    message = DeliveryMessage(text="hi")

    unwired = WebchatChannelAdapter()
    await unwired.start()
    with pytest.raises(ChannelAdapterUnavailable):
        await unwired.send(route, message)
    with pytest.raises(ChannelAdapterUnavailable):
        await unwired.stream(route, _chunks("a"))

    sink = _RecordingSink()
    wired = WebchatChannelAdapter(sink=sink)
    await wired.start()
    await wired.send(route, message)
    await wired.stream(route, _chunks("a", "b"))
    assert sink.delivered == [(route, message)]
    assert sink.streamed == [(route, ("a", "b"))]


async def test_b247_webchat_explicit_conversation_is_required_and_validated() -> None:
    """声明了显式会话能力就必须给得出：缺失/坏值显式失败，不退回隐式最新指针。"""
    from muad_contracts import ChannelEnvelope

    adapter = WebchatChannelAdapter()
    assert adapter.explicit_conversation_id(
        ChannelEnvelope(
            channel="WEBCHAT",
            bot_id=ENTRY_ID,
            external_user_id=str(PLATFORM_USER_ID),
            external_conversation_id=str(CONVERSATION_ID),
            message_id="msg-explicit-1",
        )
    ) == CONVERSATION_ID
    for raw in (None, "not-a-uuid"):
        with pytest.raises(ChannelAdapterUnavailable):
            adapter.explicit_conversation_id(
                ChannelEnvelope(
                    channel="WEBCHAT",
                    bot_id=ENTRY_ID,
                    external_user_id=str(PLATFORM_USER_ID),
                    external_conversation_id=raw,
                    message_id="msg-explicit-2",
                )
            )
