"""[S-11/TASK-006] 统一 IM 入站/出站契约与渠道内核。

真实边界：纯契约/序列化，不连服务；旧的 `ChannelEnvelope`/WebChat 契约继续可用。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from muad_contracts.channel import ChannelEnvelope
from muad_contracts.im import (
    ChannelCapabilities,
    ConnectorLease,
    DeliveryCommand,
    DeliveryKind,
    InboundEvent,
    inbound_stream_key,
    lease_key,
    outbound_stream_key,
    route_key,
    shard_for,
)
from muad_im_core import (
    AsyncAdmission,
    MessageGateway,
    MessageGatewayPorts,
    SyncAdmission,
)
from pydantic import ValidationError


def _event(**overrides: object) -> InboundEvent:
    values: dict[str, object] = {
        "event_id": uuid.uuid4(),
        "channel": "WECOM",
        "tenant_id": "tenant-a",
        "bot_id": "bot-1",
        "external_message_id": "msg-1",
        "external_conversation_id": "chat-1",
        "external_user_id": "user-1",
        "text": "hello",
        "received_at": datetime(2026, 10, 25, tzinfo=UTC),
    }
    values.update(overrides)
    return InboundEvent(**values)  # type: ignore[arg-type]


def _command(**overrides: object) -> DeliveryCommand:
    values: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "channel": "WECOM",
        "tenant_id": "tenant-a",
        "bot_id": "bot-1",
        "run_id": uuid.uuid4(),
        "external_conversation_id": "chat-1",
        "external_user_id": "user-1",
        "kind": DeliveryKind.TEXT,
        "text": "reply",
        "epoch": 1,
        "created_at": datetime(2026, 10, 25, tzinfo=UTC),
    }
    values.update(overrides)
    return DeliveryCommand(**values)  # type: ignore[arg-type]


def test_inbound_event_json_round_trip_and_unknown_field_rejected() -> None:
    event = _event()
    payload = event.model_dump(mode="json")
    assert InboundEvent.model_validate(payload) == event
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        InboundEvent.model_validate(payload)


def test_new_channel_names_are_open_in_the_new_contracts() -> None:
    """新 IM 只写 adapter：新通道名在统一契约里合法，旧 ChannelEnvelope 仍保持原词表。"""
    event = _event(channel="SLACK")
    assert InboundEvent.model_validate(event.model_dump(mode="json")).channel == "SLACK"


def test_delivery_command_round_trip_and_strict_payload() -> None:
    command = _command(payload={"text": "x", "n": 1})
    assert DeliveryCommand.model_validate(command.model_dump(mode="json")) == command
    with pytest.raises(ValidationError):
        _command(payload={"bad": float("nan")})
    with pytest.raises(ValidationError):
        _command(unexpected=1)


def test_capabilities_and_lease_round_trip() -> None:
    caps = ChannelCapabilities(
        channel="WECOM", streaming=True, media=True, max_text_bytes=4096, features=("reply",)
    )
    assert ChannelCapabilities.model_validate(caps.model_dump(mode="json")) == caps
    lease = ConnectorLease(
        channel="WECOM",
        tenant_id="tenant-a",
        bot_id="bot-1",
        owner_id="connector-1",
        epoch=3,
        expires_at=datetime(2026, 10, 25, tzinfo=UTC),
    )
    assert ConnectorLease.model_validate(lease.model_dump(mode="json")) == lease


def test_route_and_stream_keys_are_deterministic_and_channel_scoped() -> None:
    event = _event()
    same = _event(event_id=uuid.uuid4())
    assert inbound_stream_key(event) == inbound_stream_key(same)
    assert inbound_stream_key(event, shards=8).startswith("im.inbound:")
    assert inbound_stream_key(event) != inbound_stream_key(_event(bot_id="bot-2"))
    assert route_key(
        channel=event.channel,
        tenant_id=event.tenant_id,
        bot_id=event.bot_id,
        external_conversation_id=event.external_conversation_id,
        external_user_id=event.external_user_id,
    ) == route_key(
        channel=event.channel,
        tenant_id=event.tenant_id,
        bot_id=event.bot_id,
        external_conversation_id=event.external_conversation_id,
        external_user_id=event.external_user_id,
    )
    assert shard_for("k", 32) == shard_for("k", 32)
    assert 0 <= shard_for("k", 32) < 32
    with pytest.raises(ValueError):
        shard_for("k", 0)
    command = _command()
    assert outbound_stream_key(command) == outbound_stream_key(
        _command(command_id=uuid.uuid4())
    )
    assert lease_key(
        ConnectorLease(
            channel="WECOM",
            tenant_id="t",
            bot_id="b",
            owner_id="o",
            epoch=1,
            expires_at=datetime.now(UTC),
        )
    ) == "connector:bot:t:b"


class _Adapter:
    def __init__(self, channel: str = "WECOM") -> None:
        self._channel = channel

    @property
    def channel(self) -> str:
        return self._channel

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(channel=self._channel, max_text_bytes=1024)

    def normalize(self, raw: object) -> InboundEvent:
        return _event()

    async def send(self, command: DeliveryCommand) -> None:
        return None


def test_gateway_routes_sync_and_async_and_rejects_channel_mismatch() -> None:
    ports = MessageGatewayPorts(
        admit_sync=lambda event: SyncAdmission(run_id=str(uuid.uuid4())),
        admit_async=lambda event: AsyncAdmission(accepted=True),
        capabilities=lambda channel: ChannelCapabilities(channel=channel, max_text_bytes=1024),
    )
    gateway = MessageGateway(ports)
    adapter = _Adapter()
    assert gateway.admit_sync(_event(), adapter=adapter).run_id
    assert gateway.admit_async(_event(), adapter=adapter).accepted
    assert gateway.capabilities_for("WECOM").max_text_bytes == 1024
    with pytest.raises(ValueError, match="channel"):
        gateway.admit_sync(_event(channel="WEBCHAT"), adapter=adapter)


def test_legacy_channel_envelope_still_round_trips() -> None:
    envelope = ChannelEnvelope(
        channel="WEBCHAT",
        bot_id="bot-1",
        external_user_id="user-1",
        message_id="msg-1",
        text="legacy",
    )
    payload = json.loads(envelope.model_dump_json())
    assert ChannelEnvelope.model_validate(payload) == envelope
