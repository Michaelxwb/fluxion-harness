from __future__ import annotations

import ssl

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from muad_common import SharedSettings
from muad_contracts import (
    BotSnapshotItem,
    ChannelEnvelope,
    DeliveryMessage,
    DeliveryRouteInput,
)

from ..base import ChannelAdapterUnavailable
from .sdk_port import (
    EventCallback,
    MessageCallback,
    WeComInboundEvent,
    WeComInboundMessage,
    WeComSdkConnectionError,
    WeComSdkFactory,
    WeComSdkPort,
)

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF_BASE_SEC = 1.0
DEFAULT_BACKOFF_MAX_SEC = 30.0
DEFAULT_STREAM_FLUSH_INTERVAL_SEC = 0.5
# 连接存活探测间隔：SDK 不保证服务端主动关闭时回调 on_disconnected，按有界间隔兜底探测
DEFAULT_LIVENESS_INTERVAL_SEC = 1.0
TEXT_MESSAGE_TYPE = "text"
STREAM_HEADER_KEY = "headers"
STREAM_REPLY_ID_KEY = "req_id"

RouteKey = tuple[str, str, str | None]


class ConnectionState(StrEnum):
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    BACKOFF = "BACKOFF"
    DISCONNECTED = "DISCONNECTED"
    STOPPING = "STOPPING"


@dataclass(slots=True)
class _StreamState:
    stream_id: str
    reply_ref: str
    buffer: list[str] = field(default_factory=list)
    last_update_at: float = 0.0


def route_key(route: DeliveryRouteInput) -> RouteKey:
    return (route.bot_id, route.external_user_id, route.external_conversation_id)


def _envelope_key(envelope: ChannelEnvelope) -> RouteKey:
    return (envelope.bot_id, envelope.external_user_id, envelope.external_conversation_id)


def _chat_id(route: DeliveryRouteInput) -> str:
    return route.external_conversation_id or route.external_user_id


class _BotConnection:
    def __init__(
        self,
        bot: BotSnapshotItem,
        *,
        sdk_factory: WeComSdkFactory,
        emit: Callable[[WeComInboundMessage], None],
        backoff_base_sec: float,
        backoff_max_sec: float,
        liveness_interval_sec: float = DEFAULT_LIVENESS_INTERVAL_SEC,
    ) -> None:
        self.bot = bot
        self._sdk_factory = sdk_factory
        self._emit = emit
        self._backoff_base_sec = backoff_base_sec
        self._backoff_max_sec = backoff_max_sec
        self._liveness_interval_sec = liveness_interval_sec
        self._state = ConnectionState.DISCONNECTED
        self._last_error: Exception | None = None
        self._client: WeComSdkPort | None = None
        self._task: asyncio.Task[None] | None = None
        self._disconnected = asyncio.Event()
        self._stop_requested = asyncio.Event()
        self._first_attempt = asyncio.Event()

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def require_client(self) -> WeComSdkPort:
        if self._client is None or self._state is not ConnectionState.CONNECTED:
            raise ChannelAdapterUnavailable(
                f"wecom bot connection unavailable bot_id={self.bot.bot_id} state={self._state}"
            )
        return self._client

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_requested.clear()
        self._first_attempt.clear()
        self._task = asyncio.create_task(self._run())
        await self._first_attempt.wait()

    async def stop(self) -> None:
        self._stop_requested.set()
        self._state = ConnectionState.STOPPING
        if self._client is not None:
            self._client.disconnect()
            self._client = None
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._state = ConnectionState.DISCONNECTED

    async def _run(self) -> None:
        delay = self._backoff_base_sec
        while not self._stop_requested.is_set():
            self._state = ConnectionState.CONNECTING
            try:
                await self._connect_once()
            except ChannelAdapterUnavailable as exc:
                self._record_failure("wecom_bot_secret_missing", exc)
                self._state = ConnectionState.BACKOFF
                self._signal_first_attempt()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff_max_sec)
                continue
            except Exception as exc:
                self._record_failure("wecom_bot_connect_failed", exc)
                self._state = ConnectionState.BACKOFF
                self._signal_first_attempt()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff_max_sec)
                continue
            delay = self._backoff_base_sec
            self._signal_first_attempt()
            await self._wait_for_disconnect()
            if self._stop_requested.is_set():
                return
            self._state = ConnectionState.BACKOFF
            await asyncio.sleep(delay)

    async def _wait_for_disconnect(self) -> None:
        """等待断线并触发退避重连。

        SDK 在服务端主动关闭连接时不保证回调 `on_disconnected`（实测仅 `is_connected`
        变为 False），因此除回调外按固定间隔探测连接存活；间隔有界，不构成紧循环。
        """
        while not self._stop_requested.is_set():
            if self._disconnected.is_set():
                return
            client = self._client
            if client is None or not client.is_connected:
                self._handle_disconnected("connection_lost")
                return
            await asyncio.sleep(self._liveness_interval_sec)

    async def _connect_once(self) -> None:
        secret = await self._resolve_bot_secret()
        client = self._sdk_factory(self.bot.bot_id, secret)
        self._client = client
        self._wire(client)
        self._disconnected.clear()
        await client.connect()

    async def _resolve_bot_secret(self) -> str:
        if self.bot.secret:
            return self.bot.secret
        raise ChannelAdapterUnavailable("bot secret is not configured")

    def _wire(self, client: WeComSdkPort) -> None:
        client.on_message(self._handle_message)
        client.on_event(self._handle_event)
        client.on_authenticated(self._handle_authenticated)
        client.on_disconnected(self._handle_disconnected)
        client.on_error(self._handle_error)

    def _signal_first_attempt(self) -> None:
        if not self._first_attempt.is_set():
            self._first_attempt.set()

    def _record_failure(self, event: str, error: Exception) -> None:
        self._last_error = error
        logger.warning("%s bot_id=%s error=%s", event, self.bot.bot_id, type(error).__name__)

    def _handle_message(self, message: WeComInboundMessage) -> None:
        self._emit(message)

    def _handle_event(self, event: WeComInboundEvent) -> None:
        logger.debug("wecom_bot_event bot_id=%s event_type=%s", self.bot.bot_id, event.event_type)

    def _handle_authenticated(self) -> None:
        if self._stop_requested.is_set():
            return
        self._state = ConnectionState.CONNECTED
        logger.info("wecom_bot_connected bot_id=%s", self.bot.bot_id)

    def _handle_disconnected(self, reason: str) -> None:
        if self._stop_requested.is_set():
            return
        self._state = ConnectionState.BACKOFF
        self._disconnected.set()
        logger.warning("wecom_bot_disconnected bot_id=%s reason=%s", self.bot.bot_id, reason)

    def _handle_error(self, error: Exception) -> None:
        self._last_error = error
        logger.warning("wecom_bot_error bot_id=%s error=%s", self.bot.bot_id, type(error).__name__)
        if self._stop_requested.is_set():
            return
        self._state = ConnectionState.BACKOFF
        self._disconnected.set()
        if self._client is not None:
            self._client.disconnect()


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_str(source: Mapping[str, object], key: str) -> str:
    value = source.get(key)
    return value if isinstance(value, str) else ""


class _AibotClient(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def on(self, event: str, handler: Callable[..., object]) -> object: ...

    async def connect(self) -> object: ...

    def disconnect(self) -> None: ...

    async def send_message(self, chatid: str, body: dict[str, object]) -> object: ...

    async def reply_stream(
        self,
        frame: dict[str, object],
        stream_id: str,
        content: str,
        finish: bool = False,
    ) -> object: ...


class _AibotClientPort:
    def __init__(self, client: _AibotClient, *, bot_id: str) -> None:
        self._client = client
        self._bot_id = bot_id

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    def on_message(self, callback: MessageCallback) -> None:
        self._client.on("message", lambda frame: self._deliver_message(frame, callback))

    def on_event(self, callback: EventCallback) -> None:
        self._client.on("event", lambda frame: self._deliver_event(frame, callback))

    def on_authenticated(self, callback: Callable[[], None]) -> None:
        self._client.on("authenticated", lambda: callback())

    def on_disconnected(self, callback: Callable[[str], None]) -> None:
        def _handler(reason: object = None) -> None:
            callback(reason if isinstance(reason, str) else "")

        self._client.on("disconnected", _handler)

    def on_error(self, callback: Callable[[Exception], None]) -> None:
        def _handler(error: object) -> None:
            if isinstance(error, Exception):
                callback(error)

        self._client.on("error", _handler)

    async def connect(self) -> None:
        await self._client.connect()
        if not self._client.is_connected:
            raise WeComSdkConnectionError("wecom sdk connection not established")

    def disconnect(self) -> None:
        self._client.disconnect()

    async def send_text(self, chat_id: str, text: str) -> None:
        await self._client.send_message(chat_id, {"msgtype": "text", "text": {"content": text}})

    async def send_stream(self, reply_id: str, stream_id: str, content: str, *, finish: bool) -> None:
        frame: dict[str, object] = {STREAM_HEADER_KEY: {STREAM_REPLY_ID_KEY: reply_id}}
        await self._client.reply_stream(frame, stream_id, content, finish=finish)

    def _deliver_message(self, frame: object, callback: MessageCallback) -> None:
        message = self._to_inbound_message(frame)
        if message is not None:
            callback(message)

    def _deliver_event(self, frame: object, callback: EventCallback) -> None:
        event = self._to_inbound_event(frame)
        if event is not None:
            callback(event)

    def _to_inbound_message(self, frame: object) -> WeComInboundMessage | None:
        if not isinstance(frame, Mapping):
            return None
        body = _as_mapping(frame.get("body"))
        headers = _as_mapping(frame.get(STREAM_HEADER_KEY))
        if _as_str(body, "msgtype") != TEXT_MESSAGE_TYPE:
            return None
        message_id = _as_str(body, "msgid")
        user_id = _as_str(_as_mapping(body.get("from")), "userid")
        text = _as_str(_as_mapping(body.get(TEXT_MESSAGE_TYPE)), "content")
        if not message_id or not user_id or not text:
            return None
        return WeComInboundMessage(
            bot_id=self._bot_id,
            message_id=message_id,
            external_user_id=user_id,
            external_conversation_id=_as_str(body, "chatid") or None,
            message_type=TEXT_MESSAGE_TYPE,
            text=text,
            reply_id=_as_str(headers, STREAM_REPLY_ID_KEY),
        )

    def _to_inbound_event(self, frame: object) -> WeComInboundEvent | None:
        if not isinstance(frame, Mapping):
            return None
        body = _as_mapping(frame.get("body"))
        event_type = _as_str(_as_mapping(body.get("event")), "eventtype")
        if not event_type:
            return None
        return WeComInboundEvent(
            bot_id=self._bot_id,
            event_type=event_type,
            external_user_id=_as_str(_as_mapping(body.get("from")), "userid"),
            external_conversation_id=_as_str(body, "chatid") or None,
        )


def build_wecom_sdk_client(bot_id: str, secret: str) -> WeComSdkPort:
    from aibot import WSClient, WSClientOptions  # type: ignore[import-untyped]

    settings = SharedSettings()
    options = WSClientOptions(
        bot_id=bot_id,
        secret=secret,
        max_reconnect_attempts=0,
        ws_url=settings.wecom_ws_url or "",
    )
    if settings.wecom_ws_ca_file:
        _trust_local_ws_ca(settings.wecom_ws_ca_file)
    client: _AibotClient = WSClient(options)
    return _AibotClientPort(client, bot_id=bot_id)


def _trust_local_ws_ca(ca_file: str) -> None:
    """把本地真实协议探针的自签 CA 交给官方 SDK。

    SDK 把 SSL context 固定在模块级且写死 certifi，无法按连接注入；仅在显式配置
    `WECOM_WS_CA_FILE`（本地探针场景）时覆盖，默认生产路径不受影响。
    """
    import aibot.ws as sdk_ws  # type: ignore[import-untyped]

    sdk_ws._SSL_CONTEXT = ssl.create_default_context(cafile=ca_file)


class WeComAdapter:
    name = "WECOM"

    def __init__(
        self,
        *,
        sdk_factory: WeComSdkFactory = build_wecom_sdk_client,
        bots: Sequence[BotSnapshotItem] = (),
        backoff_base_sec: float = DEFAULT_BACKOFF_BASE_SEC,
        backoff_max_sec: float = DEFAULT_BACKOFF_MAX_SEC,
        stream_flush_interval_sec: float = DEFAULT_STREAM_FLUSH_INTERVAL_SEC,
        liveness_interval_sec: float = DEFAULT_LIVENESS_INTERVAL_SEC,
    ) -> None:
        self._sdk_factory = sdk_factory
        self._liveness_interval_sec = liveness_interval_sec
        self._bots: tuple[BotSnapshotItem, ...] = tuple(bots)
        self._backoff_base_sec = backoff_base_sec
        self._backoff_max_sec = backoff_max_sec
        self._stream_flush_interval_sec = stream_flush_interval_sec
        self._connections: dict[str, _BotConnection] = {}
        self._events: asyncio.Queue[ChannelEnvelope] = asyncio.Queue()
        self._reply_refs: dict[RouteKey, str] = {}
        self._streams: dict[RouteKey, _StreamState] = {}
        self._started = False

    @property
    def state(self) -> ConnectionState:
        if not self._connections:
            return ConnectionState.DISCONNECTED
        states = {connection.state for connection in self._connections.values()}
        for candidate in (
            ConnectionState.CONNECTED,
            ConnectionState.CONNECTING,
            ConnectionState.BACKOFF,
            ConnectionState.STOPPING,
        ):
            if candidate in states:
                return candidate
        return ConnectionState.DISCONNECTED

    @property
    def connection_states(self) -> dict[str, ConnectionState]:
        return {bot_id: connection.state for bot_id, connection in self._connections.items()}

    @property
    def last_error(self) -> Exception | None:
        for connection in self._connections.values():
            if connection.last_error is not None:
                return connection.last_error
        return None

    def healthy(self) -> bool:
        if not self._started:
            return False
        return any(
            connection.state is ConnectionState.CONNECTED
            for connection in self._connections.values()
        )

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for bot in self._enabled_bots():
            await self._start_bot(bot)
        secret_failure = next(
            (
                connection
                for connection in self._connections.values()
                if isinstance(connection.last_error, ChannelAdapterUnavailable)
            ),
            None,
        )
        if secret_failure is None:
            return
        failure = secret_failure.last_error
        await self.stop()
        raise ChannelAdapterUnavailable(
            f"wecom bot secret unavailable bot_id={secret_failure.bot.bot_id}"
        ) from failure

    async def stop(self) -> None:
        self._started = False
        await self._finish_all_streams()
        for connection in tuple(self._connections.values()):
            await connection.stop()
        self._connections.clear()
        self._reply_refs.clear()

    async def apply_snapshot(self, items: Sequence[BotSnapshotItem]) -> None:
        desired = {item.bot_id: item for item in items if item.enabled}
        self._bots = tuple(items)
        for bot_id in tuple(self._connections):
            connection = self._connections[bot_id]
            target = desired.get(bot_id)
            if target is None or target.secret != connection.bot.secret:
                await connection.stop()
                del self._connections[bot_id]
                self._drop_routes(bot_id)
        if not self._started:
            return
        for bot_id, bot in desired.items():
            if bot_id in self._connections:
                self._connections[bot_id].bot = bot
                continue
            await self._start_bot(bot)

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        self._ensure_started()
        return self._drain()

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        self._ensure_started()
        await self._finish_stream(route_key(route))
        client = self._require_client(route.bot_id)
        await client.send_text(_chat_id(route), message.text)

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        self._ensure_started()
        client = self._require_client(route.bot_id)
        key = route_key(route)
        reply_ref = self._reply_refs.get(key)
        if reply_ref is None:
            text = "".join([chunk async for chunk in chunks])
            if text:
                await client.send_text(_chat_id(route), text)
            return
        state = self._streams.get(key)
        if state is None:
            state = _StreamState(stream_id=uuid4().hex, reply_ref=reply_ref)
            self._streams[key] = state
        async for chunk in chunks:
            state.buffer.append(chunk)
            if self._flush_due(state):
                await self._flush_stream(client, state)

    async def finish_stream(self, route: DeliveryRouteInput) -> None:
        self._ensure_started()
        await self._finish_stream(route_key(route))

    async def _start_bot(self, bot: BotSnapshotItem) -> None:
        connection = _BotConnection(
            bot,
            sdk_factory=self._sdk_factory,
            emit=self._handle_inbound,
            backoff_base_sec=self._backoff_base_sec,
            backoff_max_sec=self._backoff_max_sec,
            liveness_interval_sec=self._liveness_interval_sec,
        )
        self._connections[bot.bot_id] = connection
        await connection.start()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        while self._started:
            envelope = await self._events.get()
            await self._finish_stream(_envelope_key(envelope))
            yield envelope

    async def _flush_stream(self, client: WeComSdkPort, state: _StreamState) -> None:
        await client.send_stream(state.reply_ref, state.stream_id, "".join(state.buffer), finish=False)
        state.last_update_at = asyncio.get_running_loop().time()

    def _flush_due(self, state: _StreamState) -> bool:
        if state.last_update_at == 0.0:
            return True
        elapsed = asyncio.get_running_loop().time() - state.last_update_at
        return elapsed >= self._stream_flush_interval_sec

    async def _finish_stream(self, key: RouteKey) -> None:
        state = self._streams.pop(key, None)
        if state is None:
            return
        text = "".join(state.buffer)
        connection = self._connections.get(key[0])
        if not text or connection is None:
            return
        try:
            client = connection.require_client()
            await client.send_stream(state.reply_ref, state.stream_id, text, finish=True)
        except Exception as exc:
            logger.warning("wecom_stream_finalize_failed bot_id=%s error=%s", key[0], type(exc).__name__)

    async def _finish_all_streams(self) -> None:
        for key in tuple(self._streams):
            await self._finish_stream(key)

    def _drop_routes(self, bot_id: str) -> None:
        for key in tuple(self._streams):
            if key[0] == bot_id:
                del self._streams[key]
        for key in tuple(self._reply_refs):
            if key[0] == bot_id:
                del self._reply_refs[key]

    def _handle_inbound(self, message: WeComInboundMessage) -> None:
        if not message.message_id or not message.external_user_id:
            logger.warning("wecom_inbound_dropped bot_id=%s reason=missing_identity", message.bot_id)
            return
        key = (message.bot_id, message.external_user_id, message.external_conversation_id)
        self._reply_refs[key] = message.reply_id
        self._events.put_nowait(
            ChannelEnvelope(
                channel="WECOM",
                bot_id=message.bot_id,
                external_user_id=message.external_user_id,
                external_conversation_id=message.external_conversation_id,
                message_id=message.message_id,
                text=message.text,
            )
        )

    def _require_client(self, bot_id: str) -> WeComSdkPort:
        connection = self._connections.get(bot_id)
        if connection is None:
            raise ChannelAdapterUnavailable(f"wecom bot connection not configured bot_id={bot_id}")
        return connection.require_client()

    def _ensure_started(self) -> None:
        if not self._started:
            raise ChannelAdapterUnavailable("wecom adapter is not started")

    def _enabled_bots(self) -> tuple[BotSnapshotItem, ...]:
        return tuple(bot for bot in self._bots if bot.enabled)
