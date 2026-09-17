from __future__ import annotations

import asyncio
import logging
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
from muad_platform_sdk import EnvSecretProvider, SecretNotFoundError

BOT_ID = "bot-1"
SECRET_REF_V1 = "secret://wecom/bot-1"
SECRET_REF_V2 = "secret://wecom/bot-1-v2"
SECRET_VALUE = "super-secret-token-9f"
SECRET_VALUE_V2 = "rotated-secret-token-4c"


def make_bot(*, secret_ref: str = SECRET_REF_V1, enabled: bool = True) -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid4(),
        bot_id=BOT_ID,
        secret_ref=secret_ref,
        agent_id=uuid4(),
        enabled=enabled,
    )


def provider() -> EnvSecretProvider:
    return EnvSecretProvider(
        {
            "MUAD_SECRET__WECOM__BOT_1": SECRET_VALUE,
            "MUAD_SECRET__WECOM__BOT_1_V2": SECRET_VALUE_V2,
        }
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
    secret_provider: EnvSecretProvider | None = None,
    stream_flush_interval_sec: float = 1000.0,
) -> WeComAdapter:
    return WeComAdapter(
        secret_provider=secret_provider or provider(),
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
    adapter = build_adapter(
        factory,
        bots=[make_bot(secret_ref="secret://wecom/missing")],
        secret_provider=EnvSecretProvider({}),
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ChannelAdapterUnavailable) as excinfo:
            await adapter.start()

    assert isinstance(excinfo.value.__cause__, SecretNotFoundError)
    assert excinfo.value.__cause__.secret_ref == "secret://wecom/missing"
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
            [bot.model_copy(update={"secret_ref": SECRET_REF_V2})]
        )

        assert factory.secrets == [(BOT_ID, SECRET_VALUE), (BOT_ID, SECRET_VALUE_V2)]
        assert first.disconnect_calls >= 1
        assert adapter.state is ConnectionState.CONNECTED
        assert adapter.healthy() is True
    finally:
        await adapter.stop()


async def test_apply_snapshot_keeps_connection_when_secret_ref_unchanged() -> None:
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
            items=[bot.model_copy(update={"secret_ref": SECRET_REF_V2})],
        )
        await cache.refresh()

        assert factory.secrets == [(BOT_ID, SECRET_VALUE), (BOT_ID, SECRET_VALUE_V2)]
        assert adapter.state is ConnectionState.CONNECTED
    finally:
        await adapter.stop()
