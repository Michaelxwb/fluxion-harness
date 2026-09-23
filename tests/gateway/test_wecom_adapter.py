from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

import pytest
from fakes import FakeConsoleClient, FakeWeComSdkFactory
from muad_contracts import (
    BotSnapshotItem,
    BotSnapshotResponse,
    ChannelEnvelope,
    DeliveryMessage,
    DeliveryRouteInput,
)
from muad_im_gateway.application.bot_snapshot import BotSnapshotCache
from muad_im_gateway.channels.base import ChannelAdapterUnavailable
from muad_im_gateway.channels.wecom.adapter import ConnectionState, WeComAdapter
from muad_im_gateway.channels.wecom.sdk_port import WeComInboundEvent, WeComInboundMessage

BOT_ID = "bot-1"
SECRET_VALUE = "super-secret-token-9f"
SECRET_VALUE_V2 = "rotated-secret-token-4c"


def make_bot(*, secret: str | None = SECRET_VALUE, enabled: bool = True) -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid4(),
        bot_id=BOT_ID,
        secret=secret,
        agent_id=uuid4(),
        enabled=enabled,
    )


def route(
    *,
    external_user_id: str = "ext-1",
    external_conversation_id: str | None = "conv-1",
) -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WECOM",
        bot_id=BOT_ID,
        external_user_id=external_user_id,
        external_conversation_id=external_conversation_id,
    )


async def chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


def make_message(
    *,
    message_id: str = "m-1",
    text: str = "你好",
    reply_id: str = "req-1",
) -> WeComInboundMessage:
    return WeComInboundMessage(
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id="ext-1",
        external_conversation_id="conv-1",
        message_type="text",
        text=text,
        reply_id=reply_id,
    )


async def wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


def build_adapter(
    factory: FakeWeComSdkFactory,
    *,
    bots: list[BotSnapshotItem] | None = None,
    stream_flush_interval_sec: float = 1000.0,
) -> WeComAdapter:
    return WeComAdapter(
        sdk_factory=factory,
        bots=bots if bots is not None else [make_bot()],
        backoff_base_sec=0.01,
        backoff_max_sec=0.02,
        stream_flush_interval_sec=stream_flush_interval_sec,
    )


async def test_start_resolves_secret_and_connects() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    await adapter.start()
    try:
        assert factory.secrets == [(BOT_ID, SECRET_VALUE)]
        assert factory.latest().connected is True
        assert adapter.state is ConnectionState.CONNECTED
        assert adapter.healthy() is True
    finally:
        await adapter.stop()


async def test_start_missing_secret_raises_typed_error_without_leaking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory, bots=[make_bot(secret=None)])
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ChannelAdapterUnavailable) as excinfo:
            await adapter.start()

    assert "bot-1" in str(excinfo.value)
    assert factory.clients == []
    assert adapter.healthy() is False
    assert SECRET_VALUE not in caplog.text


async def test_inbound_message_becomes_envelope() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    await adapter.start()
    try:
        events = await adapter.iter_events()
        factory.latest().push_message(make_message())
        envelope = await anext(events)

        assert envelope == ChannelEnvelope(
            channel="WECOM",
            bot_id=BOT_ID,
            external_user_id="ext-1",
            external_conversation_id="conv-1",
            message_id="m-1",
            text="你好",
        )
    finally:
        await adapter.stop()


async def test_inbound_event_callback_is_accepted() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    await adapter.start()
    try:
        factory.latest().push_event(
            WeComInboundEvent(
                bot_id=BOT_ID,
                event_type="enter_chat",
                external_user_id="ext-1",
                external_conversation_id=None,
            )
        )
    finally:
        await adapter.stop()


async def test_send_uses_conversation_then_user_id() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    await adapter.start()
    try:
        await adapter.send(route(), DeliveryMessage(text="first"))
        await adapter.send(
            route(external_conversation_id=None),
            DeliveryMessage(text="second"),
        )
        assert factory.latest().sent_texts == [("conv-1", "first"), ("ext-1", "second")]
    finally:
        await adapter.stop()


async def test_stream_accumulates_one_reply_and_throttles() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory, stream_flush_interval_sec=1000.0)
    await adapter.start()
    try:
        factory.latest().push_message(make_message(text="hello"))
        await adapter.stream(route(), chunks("a", "b"))
        await adapter.stream(route(), chunks("c"))
        updates = list(factory.latest().stream_updates)
        assert [(content, finish) for _, _, content, finish in updates] == [("a", False)]

        await adapter.send(route(), DeliveryMessage(text="done"))
        updates = list(factory.latest().stream_updates)
        assert updates[0][0] == "req-1"
        assert [(content, finish) for _, _, content, finish in updates] == [
            ("a", False),
            ("abc", True),
        ]
        assert {stream_id for _, stream_id, _, _ in updates} == {updates[0][1]}
        assert factory.latest().sent_texts == [("conv-1", "done")]
    finally:
        await adapter.stop()


async def test_finish_stream_finishes_open_reply() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory, stream_flush_interval_sec=1000.0)
    await adapter.start()
    try:
        factory.latest().push_message(make_message(text="hello"))
        await adapter.stream(route(), chunks("a", "b"))
        await adapter.finish_stream(route())

        assert factory.latest().stream_updates[-1][2:] == ("ab", True)

        await adapter.finish_stream(route())
        assert len(factory.latest().stream_updates) == 2
    finally:
        await adapter.stop()


async def test_stream_flushes_every_chunk_when_interval_zero() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory, stream_flush_interval_sec=0.0)
    await adapter.start()
    try:
        factory.latest().push_message(make_message(text="hello"))
        await adapter.stream(route(), chunks("a", "b"))
        await adapter.stream(route(), chunks("c"))
        updates = [content for _, _, content, finish in factory.latest().stream_updates if not finish]
        assert updates == ["a", "ab", "abc"]

        await adapter.send(route(), DeliveryMessage(text="done"))
        assert factory.latest().stream_updates[-1][2:] == ("abc", True)
    finally:
        await adapter.stop()


async def test_stream_without_inbound_falls_back_to_text_send() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    await adapter.start()
    try:
        await adapter.stream(route(), chunks("x", "y"))
        assert factory.latest().sent_texts == [("conv-1", "xy")]
        assert factory.latest().stream_updates == []
    finally:
        await adapter.stop()


async def test_disconnect_enters_backoff_and_reconnects() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    await adapter.start()
    try:
        first = factory.latest()
        first.emit_disconnected()

        await wait_for(lambda: adapter.state is ConnectionState.BACKOFF)
        await wait_for(lambda: adapter.state is ConnectionState.CONNECTED and len(factory.clients) >= 2)
        assert factory.latest().connected is True
        assert factory.latest() is not first
    finally:
        await adapter.stop()


async def test_connect_failure_enters_backoff_and_retries() -> None:
    factory = FakeWeComSdkFactory(fail_connect_times=1)
    adapter = build_adapter(factory)
    await adapter.start()
    try:
        assert adapter.state is ConnectionState.BACKOFF
        assert adapter.healthy() is False
        with pytest.raises(ChannelAdapterUnavailable):
            await adapter.send(route(), DeliveryMessage(text="too-early"))

        factory.fail_connect_times = 0
        await wait_for(lambda: adapter.state is ConnectionState.CONNECTED)
        assert len(factory.clients) == 2
        assert factory.latest().connected is True
    finally:
        await adapter.stop()


async def test_adapter_logs_do_not_contain_secret_values(caplog: pytest.LogCaptureFixture) -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    with caplog.at_level(logging.DEBUG):
        await adapter.start()
        try:
            factory.latest().push_message(make_message())
            await adapter.send(route(), DeliveryMessage(text="done"))
        finally:
            await adapter.stop()

    assert SECRET_VALUE not in caplog.text


async def test_stop_disconnects_and_blocks_sends() -> None:
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory)
    await adapter.start()
    port = factory.latest()
    await adapter.stop()

    assert port.disconnect_calls == 1
    assert port.connected is False
    assert adapter.state is ConnectionState.DISCONNECTED
    assert adapter.healthy() is False
    with pytest.raises(ChannelAdapterUnavailable):
        await adapter.send(route(), DeliveryMessage(text="x"))
    with pytest.raises(ChannelAdapterUnavailable):
        await adapter.iter_events()


async def test_apply_snapshot_restarts_connection_on_secret_change() -> None:
    factory = FakeWeComSdkFactory()
    bot = make_bot()
    adapter = build_adapter(factory, bots=[bot])
    await adapter.start()
    try:
        first = factory.latest()
        await adapter.apply_snapshot(
            [bot.model_copy(update={"secret": SECRET_VALUE_V2})]
        )

        assert factory.secrets == [(BOT_ID, SECRET_VALUE), (BOT_ID, SECRET_VALUE_V2)]
        assert first.disconnect_calls >= 1
        assert adapter.state is ConnectionState.CONNECTED
        assert adapter.healthy() is True
    finally:
        await adapter.stop()


async def test_apply_snapshot_keeps_connection_when_secret_unchanged() -> None:
    factory = FakeWeComSdkFactory()
    bot = make_bot()
    adapter = build_adapter(factory, bots=[bot])
    await adapter.start()
    try:
        await adapter.apply_snapshot([bot.model_copy(update={"agent_id": uuid4()})])
        assert len(factory.clients) == 1
        assert adapter.state is ConnectionState.CONNECTED
    finally:
        await adapter.stop()


async def test_apply_snapshot_removes_disabled_bot() -> None:
    factory = FakeWeComSdkFactory()
    bot = make_bot()
    adapter = build_adapter(factory, bots=[bot])
    await adapter.start()
    try:
        first = factory.latest()
        await adapter.apply_snapshot([bot.model_copy(update={"enabled": False})])
        assert first.disconnect_calls >= 1
        assert adapter.connection_states == {}
        assert adapter.healthy() is False
    finally:
        await adapter.stop()


async def test_snapshot_revision_change_refreshes_adapter() -> None:
    console = FakeConsoleClient()
    bot = make_bot()
    console.bot_snapshot = BotSnapshotResponse(revision="r1", items=[bot])
    factory = FakeWeComSdkFactory()
    adapter = build_adapter(factory, bots=[])
    cache = BotSnapshotCache(
        console,
        tenant_id="t1",
        on_snapshot_changed=adapter.apply_snapshot,
    )
    await cache.refresh()
    await adapter.start()
    try:
        assert factory.secrets == [(BOT_ID, SECRET_VALUE)]

        console.bot_snapshot = BotSnapshotResponse(
            revision="r2",
            items=[bot.model_copy(update={"secret": SECRET_VALUE_V2})],
        )
        await cache.refresh()

        assert factory.secrets == [(BOT_ID, SECRET_VALUE), (BOT_ID, SECRET_VALUE_V2)]
        assert adapter.state is ConnectionState.CONNECTED
    finally:
        await adapter.stop()


# ---------------------------------------------------------------------------
# B-106: 多 Bot 故障隔离与 WS 退避（真实本地 WS 故障探针）
# ---------------------------------------------------------------------------


B106_GOOD_BOT = "bot-b106-good"
B106_BAD_BOT = "bot-b106-bad"
B106_SECRET = "b106-secret"


def _b106_bot(bot_id: str):
    return BotSnapshotItem(
        bot_account_id=uuid4(), bot_id=bot_id, secret=B106_SECRET, agent_id=uuid4()
    )


class _B106ProbeFixture:
    """探针 + 环境 seam（WECOM_WS_URL / WECOM_WS_CA_FILE）下的生产 Adapter。"""

    def __init__(self, probe, adapter) -> None:  # noqa: ANN001 - 测试内部
        self.probe = probe
        self.adapter = adapter


async def _b106_stack(monkeypatch, bots: list[str]):  # noqa: ANN001
    from tests.e2e.wecom_probe_app import WeComProbe

    probe = WeComProbe(expected_bots={bot_id: B106_SECRET for bot_id in bots})
    await probe.start()
    monkeypatch.setenv("WECOM_WS_URL", probe.ws_url)
    monkeypatch.setenv("WECOM_WS_CA_FILE", str(probe.cert_path))
    adapter = WeComAdapter(
        bots=[_b106_bot(bot_id) for bot_id in bots],
        backoff_base_sec=0.2,
        backoff_max_sec=0.5,
        liveness_interval_sec=0.2,
    )
    await adapter.start()
    return _B106ProbeFixture(probe, adapter)


async def _b106_wait(predicate, timeout: float = 15.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_b106_disconnect_is_recovered_by_backoff_reconnect(monkeypatch) -> None:  # noqa: ANN001
    fixture = await _b106_stack(monkeypatch, [B106_GOOD_BOT])
    probe, adapter = fixture.probe, fixture.adapter
    try:
        assert await _b106_wait(
            lambda: adapter.connection_states.get(B106_GOOD_BOT) is ConnectionState.CONNECTED
        )
        initial = len(probe.connections)
        await probe.drop_connection(B106_GOOD_BOT)
        # 服务端断线必须被观测并经真实 socket 重新握手（SDK 不保证回调，Adapter 侧兜底探测）
        assert await _b106_wait(lambda: len(probe.connections) > initial), "断线后未重连"
        assert await _b106_wait(
            lambda: adapter.connection_states.get(B106_GOOD_BOT) is ConnectionState.CONNECTED
        )
    finally:
        await adapter.stop()
        await probe.stop()


async def test_b106_bad_bot_backoff_does_not_block_good_bot(monkeypatch) -> None:  # noqa: ANN001
    fixture = await _b106_stack(monkeypatch, [B106_GOOD_BOT, B106_BAD_BOT])
    probe, adapter = fixture.probe, fixture.adapter
    probe.reject_bot_ids.add(B106_BAD_BOT)
    try:
        assert await _b106_wait(
            lambda: adapter.connection_states.get(B106_GOOD_BOT) is ConnectionState.CONNECTED
        )
        await asyncio.sleep(0.5)
        assert adapter.connection_states.get(B106_BAD_BOT) is not ConnectionState.CONNECTED

        # 好 bot 的入站链路不受坏 bot 影响
        route = DeliveryRouteInput(
            channel="WECOM",
            bot_id=B106_GOOD_BOT,
            external_user_id="ext-b106",
            external_conversation_id="chat-b106",
        )
        await probe.push_message(
            bot_id=B106_GOOD_BOT,
            message_id="msg-b106",
            external_user_id="ext-b106",
            text="隔离验证",
            reply_id="reply-b106",
            chat_id="chat-b106",
        )
        iterator = await adapter.iter_events()
        envelope = await asyncio.wait_for(anext(iterator), timeout=5.0)
        assert envelope.bot_id == B106_GOOD_BOT and envelope.text == "隔离验证"
        assert route.bot_id == envelope.bot_id
    finally:
        await adapter.stop()
        await probe.stop()


async def test_b106_disabling_bot_stops_only_that_connection(monkeypatch) -> None:  # noqa: ANN001
    fixture = await _b106_stack(monkeypatch, [B106_GOOD_BOT, B106_BAD_BOT])
    probe, adapter = fixture.probe, fixture.adapter
    try:
        assert await _b106_wait(
            lambda: adapter.connection_states.get(B106_BAD_BOT) is ConnectionState.CONNECTED
        )
        # 停用坏 bot：只停止目标连接，好 bot 保持
        await adapter.apply_snapshot(
            [_b106_bot(B106_GOOD_BOT)]
        )
        assert await _b106_wait(lambda: B106_BAD_BOT not in adapter.connection_states)
        assert adapter.connection_states.get(B106_GOOD_BOT) is ConnectionState.CONNECTED
    finally:
        await adapter.stop()
        await probe.stop()


async def test_b106_reconnect_attempts_are_backed_off_not_a_tight_loop(monkeypatch) -> None:  # noqa: ANN001
    fixture = await _b106_stack(monkeypatch, [B106_BAD_BOT])
    probe, adapter = fixture.probe, fixture.adapter
    probe.reject_bot_ids.add(B106_BAD_BOT)
    try:
        await asyncio.sleep(3.0)
        attempts = len(probe.frames_of("aibot_subscribe"))
        # backoff base=0.2 / max=0.5：3 秒内尝试次数有界（远小于紧循环的量级）
        assert 1 <= attempts <= 20, f"重连次数异常（疑似紧循环）：{attempts}"
        assert adapter.connection_states.get(B106_BAD_BOT) is not ConnectionState.CONNECTED
    finally:
        await adapter.stop()
        await probe.stop()
