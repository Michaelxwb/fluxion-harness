from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class WeComSdkError(RuntimeError):
    pass


class WeComSdkConnectionError(WeComSdkError):
    pass


@dataclass(frozen=True, slots=True)
class WeComInboundMessage:
    bot_id: str
    message_id: str
    external_user_id: str
    external_conversation_id: str | None
    message_type: str
    text: str
    reply_id: str


@dataclass(frozen=True, slots=True)
class WeComInboundEvent:
    bot_id: str
    event_type: str
    external_user_id: str
    external_conversation_id: str | None


MessageCallback = Callable[[WeComInboundMessage], None]
EventCallback = Callable[[WeComInboundEvent], None]
AuthenticatedCallback = Callable[[], None]
DisconnectedCallback = Callable[[str], None]
ErrorCallback = Callable[[Exception], None]


class WeComSdkPort(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def on_message(self, callback: MessageCallback) -> None: ...

    def on_event(self, callback: EventCallback) -> None: ...

    def on_authenticated(self, callback: AuthenticatedCallback) -> None: ...

    def on_disconnected(self, callback: DisconnectedCallback) -> None: ...

    def on_error(self, callback: ErrorCallback) -> None: ...

    async def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    async def send_text(self, chat_id: str, text: str) -> None:
        """**主动发送**（`aibot_send_msg`）：用于任务结果等主动投递，不依附回调。

        不要用它回入站回调——官方服务对会话内回复只接受 `aibot_respond_msg`
        （实测以 `errcode=40008 invalid message type` 拒收 `aibot_send_msg`）；
        会话内回复用 `reply_text`。
        """
        ...

    async def reply_text(self, reply_id: str, text: str) -> None:
        """**会话内回复**（`aibot_respond_msg` + 入站回调的 `req_id`）。

        2026-09-30 真机复验：用 `send_text` 回「绑定成功」被企业微信以
        `errcode=40008 invalid message type` 拒收；改用本方法（带回调 req_id）后
        文本回复可正常送达。
        """
        ...

    async def send_stream(
        self,
        reply_id: str,
        stream_id: str,
        content: str,
        *,
        finish: bool,
    ) -> None: ...


WeComSdkFactory = Callable[[str, str], WeComSdkPort]
