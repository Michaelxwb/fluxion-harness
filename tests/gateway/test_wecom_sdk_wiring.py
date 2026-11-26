from __future__ import annotations

import re
import ssl
from collections.abc import Callable
from typing import Any

import pytest
from muad_im_connector.channels.wecom.adapter import _AibotClientPort, build_wecom_sdk_client
from muad_im_connector.channels.wecom.sdk_logging import LocalTimeSdkLogger
from muad_im_connector.channels.wecom.sdk_port import (
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


async def test_sdk_send_text_builds_markdown_payload() -> None:
    """主动投递必须用 markdown 体：官方 `aibot_send_msg` 只支持 markdown / template_card，
    `msgtype=text` 会被服务端以 `errcode=40008 invalid message type` 拒收（2026-09-30 真机实测）。"""
    raw = StubRawClient()
    await build_port(raw).send_text("conv-1", "hello")

    assert raw.sent_messages == [
        ("conv-1", {"msgtype": "markdown", "markdown": {"content": "hello"}})
    ]


async def test_sdk_reply_text_uses_callback_req_id_and_finished_stream_body() -> None:
    """会话内文本回复：带**入站回调的 req_id**，且体是 `stream` + `finish=True`（回复体不支持 text）。"""
    raw = StubRawClient()
    await build_port(raw).reply_text("req-ink-9", "绑定成功")

    frame, stream_id, content, finish = raw.stream_replies[0]
    assert frame == {"headers": {"req_id": "req-ink-9"}}
    assert isinstance(stream_id, str) and stream_id
    assert (content, finish) == ("绑定成功", True)


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


def _stub_shared_settings(monkeypatch: pytest.MonkeyPatch, *, max_tls: str | None) -> None:
    """不让装配读开发机的 `.env`：本用例只验证「设置 → 端口」这段映射。"""
    from muad_common import SharedSettings

    def factory() -> SharedSettings:
        return SharedSettings(_env_file=None, wecom_media_max_tls_version=max_tls)

    monkeypatch.setattr(
        "muad_im_connector.channels.wecom.adapter.SharedSettings", factory
    )


def test_build_wecom_sdk_client_applies_the_media_tls_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """`WECOM_MEDIA_MAX_TLS_VERSION` 落在媒体下载端口上（装配期构造，不是下载时临时解析）。"""
    _stub_shared_settings(monkeypatch, max_tls="TLSv1_2")

    port = build_wecom_sdk_client("bot-1", "unit-test-secret")
    try:
        context = port._media_ssl_context  # noqa: SLF001 - 装配契约由本用例锁定
        assert context is not None
        assert context.maximum_version is ssl.TLSVersion.TLSv1_2
        assert context.verify_mode is ssl.CERT_REQUIRED
    finally:
        port.disconnect()


def test_build_wecom_sdk_client_keeps_default_handshake_without_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_shared_settings(monkeypatch, max_tls=None)

    port = build_wecom_sdk_client("bot-1", "unit-test-secret")
    try:
        assert port._media_ssl_context is None  # noqa: SLF001 - 默认不设上限
    finally:
        port.disconnect()


#: 与 `muad_logging.formatter` 相同的行首：本地时区 + 毫秒 + `%z`。
SDK_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} [+-]\d{4} \[AiBotSDK\] ")


@pytest.mark.parametrize(
    ("method", "level"),
    [("debug", "DEBUG"), ("info", "INFO"), ("warn", "WARN"), ("error", "ERROR")],
)
def test_sdk_logger_changes_only_the_timestamp(
    capsys: pytest.CaptureFixture[str], method: str, level: str
) -> None:
    """SDK 日志只统一时间格式：stderr、前缀、级别标签、消息体与 `*args` 语义都保持原样。"""
    getattr(LocalTimeSdkLogger(), method)("received %s", "frame")

    line = capsys.readouterr().err
    assert SDK_LINE_RE.match(line) is not None, line
    assert line.endswith(f"[{level}] received %s frame\n"), line


def test_build_wecom_sdk_client_uses_the_local_time_sdk_logger() -> None:
    port = build_wecom_sdk_client("bot-1", "unit-test-secret")
    try:
        assert isinstance(port._client._logger, LocalTimeSdkLogger)  # noqa: SLF001 - 装配契约
    finally:
        port.disconnect()
