"""B-120: 真实 WS 探针与官方 SDK 边界（认证 / 消息 / 流式收发）。

不得 Mock 的真实边界：官方 `wecom-aibot-python-sdk` → 真实本地 WebSocket 服务（探针）
→ 生产 `WeComAdapter`。探针仅替代外部企业微信端点，不替代生产 Adapter/SDK，
也不代表企业微信实网已验收。
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from muad_contracts import BotSnapshotItem, DeliveryMessage, DeliveryRouteInput
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter

from tests.e2e.wecom_probe_app import WeComProbe

BOT_ID = "bot-probe-1"
BOT_SECRET = "probe-secret-1"
EXTERNAL_USER_ID = "ext-probe-1"
CHAT_ID = "chat-probe-1"


def _probe_sdk_factory(probe: WeComProbe):
    """真实官方 SDK + 生产端口包装，仅把 ws_url 指向本地探针（其余保持生产接线）。"""

    def factory(bot_id: str, secret: str):
        from aibot import WSClient, WSClientOptions  # type: ignore[import-untyped]

        from muad_im_gateway.channels.wecom.adapter import _AibotClientPort

        options = WSClientOptions(
            bot_id=bot_id,
            secret=secret,
            ws_url=probe.ws_url,
            max_reconnect_attempts=0,
            heartbeat_interval=1000,
            request_timeout=3000,
        )
        return _AibotClientPort(WSClient(options), bot_id=bot_id)

    return factory


async def _chunks(values: list[str]) -> AsyncIterator[str]:
    for value in values:
        yield value


async def _wait_connected(adapter: WeComAdapter, bot_id: str, timeout: float = 10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if adapter.connection_states.get(bot_id) is ConnectionState.CONNECTED:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"bot {bot_id} 未在 {timeout}s 内 CONNECTED："
        f"state={adapter.connection_states.get(bot_id)} last_error={adapter.last_error!r}"
    )


async def _next_envelope(adapter: WeComAdapter, timeout: float = 5.0):
    iterator = await adapter.iter_events()
    return await asyncio.wait_for(anext(iterator), timeout=timeout)


@pytest.fixture()
async def probe(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WeComProbe]:
    # 本地探针用自签证书：仅放宽客户端校验，协议与传输（真实 TLS/WS）保持不变
    import aibot.ws as sdk_ws

    monkeypatch.setattr(sdk_ws, "_SSL_CONTEXT", ssl._create_unverified_context())
    server = WeComProbe(expected_bots={BOT_ID: BOT_SECRET})
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture()
async def adapter(probe: WeComProbe) -> AsyncIterator[WeComAdapter]:
    instance = WeComAdapter(
        sdk_factory=_probe_sdk_factory(probe),
        bots=[
            BotSnapshotItem(
                bot_account_id=uuid4(),
                bot_id=BOT_ID,
                secret=BOT_SECRET,
                agent_id=uuid4(),
            )
        ],
        backoff_base_sec=0.05,
        backoff_max_sec=0.2,
    )
    await instance.start()
    await _wait_connected(instance, BOT_ID)
    try:
        yield instance
    finally:
        await instance.stop()


async def test_b120_authenticates_over_real_socket(probe: WeComProbe, adapter: WeComAdapter) -> None:
    assert adapter.connection_states[BOT_ID] is ConnectionState.CONNECTED

    subscribe_frames = probe.frames_of("aibot_subscribe")
    assert len(subscribe_frames) == 1
    assert subscribe_frames[0]["body"] == {"bot_id": BOT_ID, "secret": BOT_SECRET}
    assert probe.auth_failures == []

    # 心跳经真实 socket 往返（SDK 未判死连接即说明探针按协议 ack）
    await asyncio.sleep(1.2)
    assert probe.frames_of("ping"), "心跳帧应经真实 socket 到达探针"


async def test_b120_receives_inbound_message_frame(probe: WeComProbe, adapter: WeComAdapter) -> None:
    message_id = f"msg-{uuid4().hex[:8]}"
    await probe.push_message(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=EXTERNAL_USER_ID,
        text="帮我检查设备",
        reply_id=f"reply-{uuid4().hex[:8]}",
        chat_id=CHAT_ID,
    )
    envelope = await _next_envelope(adapter)
    assert envelope.bot_id == BOT_ID
    assert envelope.message_id == message_id
    assert envelope.external_user_id == EXTERNAL_USER_ID
    assert envelope.external_conversation_id == CHAT_ID
    assert envelope.text == "帮我检查设备"


async def test_b120_streams_reply_frames_back_through_socket(
    probe: WeComProbe, adapter: WeComAdapter
) -> None:
    reply_id = f"reply-{uuid4().hex[:8]}"
    await probe.push_message(
        bot_id=BOT_ID,
        message_id=f"msg-{uuid4().hex[:8]}",
        external_user_id=EXTERNAL_USER_ID,
        text="流式请求",
        reply_id=reply_id,
        chat_id=CHAT_ID,
    )
    await _next_envelope(adapter)

    route = DeliveryRouteInput(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id=EXTERNAL_USER_ID,
        external_conversation_id=CHAT_ID,
    )
    await adapter.stream(route, _chunks(["你", "好", "世界"]))
    await adapter.finish_stream(route)  # 收尾 flush（finish=True）
    replies = await probe.wait_for_replies(1)

    stream_replies = [frame for frame in replies if frame.get("cmd") == "aibot_respond_msg"]
    assert stream_replies, "流式回复必须经真实 socket 发出"
    assert {frame["headers"]["req_id"] for frame in stream_replies} == {reply_id}
    bodies = [frame["body"] for frame in stream_replies]
    assert all(body["msgtype"] == "stream" for body in bodies)
    assert bodies[-1]["stream"]["finish"] is True  # 收尾帧
    assert bodies[-1]["stream"]["content"] == "你好世界"  # 内容经真实协议回读


async def test_b120_proactive_send_uses_real_socket(probe: WeComProbe, adapter: WeComAdapter) -> None:
    route = DeliveryRouteInput(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id=EXTERNAL_USER_ID,
        external_conversation_id=CHAT_ID,
    )
    await adapter.send(route, DeliveryMessage(type="text", text="主动通知"))
    replies = await probe.wait_for_replies(1)
    sent = [frame for frame in replies if frame.get("cmd") == "aibot_send_msg"]
    assert len(sent) == 1
    assert sent[0]["body"]["text"]["content"] == "主动通知"
