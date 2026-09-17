from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from fakes import make_envelope
from muad_contracts import BotSnapshotItem, DeliveryMessage, DeliveryRouteInput
from muad_im_gateway.channels.base import (
    ChannelAdapter,
    ChannelAdapterUnavailable,
    ChannelRegistry,
    DuplicateChannelAdapterError,
    UnknownChannelAdapterError,
)
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.channels.wecom.adapter import WeComAdapter
from muad_platform_sdk import EnvSecretProvider


def _route() -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WECOM",
        bot_id="bot-1",
        external_user_id="ext-1",
        external_conversation_id="conv-1",
    )


def _bot(secret_ref: str = "secret://wecom/bot-1") -> BotSnapshotItem:
    return BotSnapshotItem(
        bot_account_id=uuid4(),
        bot_id="bot-1",
        secret_ref=secret_ref,
        agent_id=uuid4(),
    )


async def _chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


def test_registry_register_and_get() -> None:
    registry = ChannelRegistry()
    adapter = FakeChannelAdapter()
    registry.register(adapter)
    assert registry.get("WECOM") is adapter
    assert registry.names() == ("WECOM",)


def test_registry_rejects_duplicate_names() -> None:
    registry = ChannelRegistry()
    registry.register(FakeChannelAdapter())
    with pytest.raises(DuplicateChannelAdapterError):
        registry.register(FakeChannelAdapter())


def test_registry_reports_unknown_channel() -> None:
    registry = ChannelRegistry()
    with pytest.raises(UnknownChannelAdapterError):
        registry.get("WECOM")


async def test_registry_start_skips_unavailable_adapters() -> None:
    registry = ChannelRegistry()
    registry.register(
        WeComAdapter(secret_provider=EnvSecretProvider({}), bots=[_bot()])
    )
    await registry.start_all()
    assert registry.adapter_states == {"WECOM": False}
    assert registry.started_adapters == ()


async def test_registry_start_and_stop_track_state() -> None:
    registry = ChannelRegistry()
    adapter = FakeChannelAdapter()
    registry.register(adapter)
    await registry.start_all()
    assert registry.adapter_states == {"WECOM": True}
    assert adapter.started is True
    await registry.stop_all()
    assert registry.adapter_states == {"WECOM": False}
    assert adapter.started is False


async def test_wecom_adapter_requires_started_connection() -> None:
    adapter = WeComAdapter(secret_provider=EnvSecretProvider({}))
    with pytest.raises(ChannelAdapterUnavailable):
        await adapter.send(_route(), DeliveryMessage(text="hi"))
    with pytest.raises(ChannelAdapterUnavailable):
        await adapter.stream(_route(), _chunks("a"))
    with pytest.raises(ChannelAdapterUnavailable):
        await adapter.iter_events()
    assert adapter.healthy() is False


async def test_fake_adapter_records_send_and_stream() -> None:
    adapter = FakeChannelAdapter()
    route = _route()
    message = DeliveryMessage(text="hi")
    await adapter.send(route, message)
    await adapter.stream(route, _chunks("a", "b"))
    assert adapter.sent == ((route, message),)
    assert adapter.streamed == ((route, ("a", "b")),)


async def test_fake_adapter_iter_events_yields_pushed_envelopes() -> None:
    adapter = FakeChannelAdapter()
    envelope = make_envelope()
    await adapter.push(envelope)
    events = await adapter.iter_events()
    assert await anext(events) == envelope


async def test_fake_adapter_satisfies_channel_adapter_members() -> None:
    adapter: ChannelAdapter = FakeChannelAdapter()
    assert adapter.name == "WECOM"
    assert callable(adapter.start)
    assert callable(adapter.stop)
    assert callable(adapter.iter_events)
    assert callable(adapter.send)
    assert callable(adapter.stream)
