"""[S-11/TASK-006] 统一 IM 入站/出站契约与渠道内核。

真实边界：纯契约/序列化，不连服务；旧的 `ChannelEnvelope`/WebChat 契约继续可用。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from muad_contracts import RunStatus
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
from muad_contracts.webchat import WebchatExecutionStatus
from muad_im_core import (
    AsyncAdmission,
    MessageGateway,
    MessageGatewayPorts,
    SyncAdmission,
    SyncPrincipal,
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


def test_artifact_command_requires_the_artifact_payload() -> None:
    from muad_contracts.channel import AttachmentRef

    artifact = AttachmentRef(
        storage_key="run/r1/a1",
        kind="DOCUMENT",
        media_type="application/pdf",
        size=10,
        checksum="a" * 64,
        artifact_id=uuid.uuid4(),
    )
    command = _command(
        kind=DeliveryKind.ARTIFACT, artifact=artifact, payload={}, text=""
    )
    assert DeliveryCommand.model_validate(command.model_dump(mode="json")) == command
    with pytest.raises(ValidationError):
        _command(kind=DeliveryKind.ARTIFACT, artifact=None)


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
    assert outbound_stream_key(command) == f"im.outbound:{command.tenant_id}:{command.bot_id}"
    # 出站按 bot 分流：同 bot 的不同用户/会话仍进同一条流（由 Connector 单 owner 串行消费）。
    assert outbound_stream_key(command) == outbound_stream_key(
        _command(external_user_id="user-2", external_conversation_id="chat-2")
    )
    assert outbound_stream_key(command) != outbound_stream_key(_command(bot_id="bot-2"))
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
    """核心的通道一致性校验与能力路由是纯同步判断（受理端口本身是异步的）。"""
    ports = MessageGatewayPorts(
        admit_async=lambda event: AsyncAdmission(accepted=True),
        capabilities=lambda channel: ChannelCapabilities(channel=channel, max_text_bytes=1024),
    )
    gateway = MessageGateway(ports)
    adapter = _Adapter()
    assert gateway.admit_async(_event(), adapter=adapter).accepted
    assert gateway.capabilities_for("WECOM").max_text_bytes == 1024
    with pytest.raises(ValueError, match="channel"):
        gateway.admit_async(_event(channel="WEBCHAT"), adapter=adapter)


async def test_gateway_sync_admission_hands_the_verified_principal_to_the_port() -> None:
    """SYNC 受理：入口已验证的 principal 原样进入端口，核心不再解析身份。"""
    seen: list[SyncPrincipal] = []
    admission = SyncAdmission(
        run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        status=RunStatus.CREATED,
        execution_status=WebchatExecutionStatus(stage="RUNNING"),
    )

    async def admit_sync(event: InboundEvent, principal: SyncPrincipal) -> SyncAdmission:
        seen.append(principal)
        return admission

    gateway = MessageGateway(MessageGatewayPorts(admit_sync=admit_sync))
    principal = SyncPrincipal(
        tenant_id="tenant-a",
        platform_user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        idempotency_key="idem-sync-1",
    )
    accepted = await gateway.admit_sync(
        _event(channel="WEBCHAT"), adapter=_Adapter("WEBCHAT"), principal_context=principal
    )
    assert accepted == admission
    assert seen == [principal]
    with pytest.raises(ValueError, match="channel"):
        await gateway.admit_sync(_event(), adapter=_Adapter("WEBCHAT"), principal_context=principal)


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
