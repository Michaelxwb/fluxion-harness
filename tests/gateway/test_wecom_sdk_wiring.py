from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from muad_im_gateway.channels.wecom.adapter import _AibotClientPort, build_wecom_sdk_client
from muad_im_gateway.channels.wecom.sdk_port import (
    WeComInboundEvent,
    WeComInboundMessage,
    WeComSdkConnectionError,
)

TEXT_FRAME: dict[str, object] = {
    "cmd": "aibot_msg_callback",
    "headers": {"req_id": "req-1"},
    "body": {
        "msgid": "m-1",
        "chatid": "conv-1",
        "from": {"userid": "ext-1"},
        "msgtype": "text",
        "text": {"content": "hi"},
    },
}

EVENT_FRAME: dict[str, object] = {
    "cmd": "aibot_event_callback",
    "headers": {"req_id": "req-2"},
    "body": {
        "from": {"userid": "ext-1"},
        "msgtype": "event",
        "event": {"eventtype": "enter_chat"},
    },
}


class StubRawClient:
    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.sent_messages: list[tuple[str, dict[str, object]]] = []
        self.stream_replies: list[tuple[dict[str, object], str, str, bool]] = []
        self.connect_calls = 0
        self.disconnect_calls = 0

    @property
    def is_connected(self) -> bool:
        return self.connected

    def on(self, event: str, handler: Callable[..., Any]) -> object:
        self.handlers[event] = handler
        return self

    async def connect(self) -> object:
        self.connect_calls += 1
        return self

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def send_message(self, chatid: str, body: dict[str, object]) -> object:
        self.sent_messages.append((chatid, body))
        return {}

    async def reply_stream(
        self,
        frame: dict[str, object],
        stream_id: str,
        content: str,
        finish: bool = False,
    ) -> object:
        self.stream_replies.append((frame, stream_id, content, finish))
        return {}


def build_port(raw: StubRawClient) -> _AibotClientPort:
    return _AibotClientPort(raw, bot_id="bot-1")


def test_sdk_message_frame_maps_to_inbound_message() -> None:
    raw = StubRawClient()
    received: list[WeComInboundMessage] = []
    build_port(raw).on_message(received.append)
    raw.handlers["message"](TEXT_FRAME)

    assert received == [
        WeComInboundMessage(
            bot_id="bot-1",
            message_id="m-1",
            external_user_id="ext-1",
            external_conversation_id="conv-1",
            message_type="text",
            text="hi",
            reply_id="req-1",
        )
    ]


@pytest.mark.parametrize(
    "frame",
    [
        "not-a-frame",
        {"body": {}},
        {"body": {"msgtype": "image", "msgid": "m-1", "from": {"userid": "ext-1"}}},
        {"body": {"msgtype": "text", "msgid": "", "from": {"userid": "ext-1"}}},
        {"body": {"msgtype": "text", "msgid": "m-1", "from": {}}},
        {"body": {"msgtype": "text", "msgid": "m-1", "from": {"userid": "ext-1"}, "text": {}}},
    ],
)
def test_sdk_message_frame_invalid_payloads_are_ignored(frame: object) -> None:
    raw = StubRawClient()
    received: list[WeComInboundMessage] = []
    build_port(raw).on_message(received.append)
    raw.handlers["message"](frame)

    assert received == []


def test_sdk_event_frame_maps_to_inbound_event() -> None:
    raw = StubRawClient()
    received: list[WeComInboundEvent] = []
    build_port(raw).on_event(received.append)
    raw.handlers["event"](EVENT_FRAME)

    assert received == [
        WeComInboundEvent(
            bot_id="bot-1",
            event_type="enter_chat",
            external_user_id="ext-1",
            external_conversation_id=None,
        )
    ]


def test_sdk_event_frame_without_eventtype_is_ignored() -> None:
    raw = StubRawClient()
    received: list[WeComInboundEvent] = []
    build_port(raw).on_event(received.append)
    raw.handlers["event"]({"body": {"msgtype": "event", "event": {}}})

    assert received == []


async def test_sdk_send_text_builds_text_payload() -> None:
    raw = StubRawClient()
    await build_port(raw).send_text("conv-1", "hello")

    assert raw.sent_messages == [("conv-1", {"msgtype": "text", "text": {"content": "hello"}})]


async def test_sdk_send_stream_passes_reply_id() -> None:
    raw = StubRawClient()
    await build_port(raw).send_stream("req-1", "stream-1", "partial", finish=False)

    frame, stream_id, content, finish = raw.stream_replies[0]
    assert frame == {"headers": {"req_id": "req-1"}}
    assert (stream_id, content, finish) == ("stream-1", "partial", False)


async def test_sdk_connect_raises_typed_error_when_socket_not_open() -> None:
    raw = StubRawClient(connected=False)
    with pytest.raises(WeComSdkConnectionError):
        await build_port(raw).connect()


def test_sdk_disconnect_and_notifications_are_forwarded() -> None:
    raw = StubRawClient()
    port = build_port(raw)
    reasons: list[str] = []
    errors: list[Exception] = []
    authenticated: list[bool] = []
    port.on_disconnected(reasons.append)
    port.on_error(errors.append)
    port.on_authenticated(lambda: authenticated.append(True))

    raw.handlers["disconnected"]("code: 1006")
    raw.handlers["disconnected"]()
    raw.handlers["error"](RuntimeError("boom"))
    raw.handlers["error"]("not-an-exception")
    raw.handlers["authenticated"]()

    assert reasons == ["code: 1006", ""]
    assert [type(error).__name__ for error in errors] == ["RuntimeError"]
    assert authenticated == [True]

    port.disconnect()
    assert raw.disconnect_calls == 1


def test_build_wecom_sdk_client_constructs_port_without_connecting() -> None:
    port = build_wecom_sdk_client("bot-1", "unit-test-secret")

    assert port.is_connected is False
    port.disconnect()
