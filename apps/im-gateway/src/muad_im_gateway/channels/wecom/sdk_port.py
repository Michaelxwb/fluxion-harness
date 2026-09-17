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

    async def send_text(self, chat_id: str, text: str) -> None: ...

    async def send_stream(
        self,
        reply_id: str,
        stream_id: str,
        content: str,
        *,
        finish: bool,
    ) -> None: ...


WeComSdkFactory = Callable[[str, str], WeComSdkPort]
