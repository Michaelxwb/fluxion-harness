from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from muad_api import metrics
from muad_api.context import current_trace_id
from muad_common import SharedSettings
from muad_contracts import (
    BotSnapshotItem,
    ChannelEnvelope,
    DeliveryMessage,
    DeliveryRouteInput,
    UnsupportedMedia,
)

from ..base import (
    ATTACHMENT_DECRYPT_FAILED,
    ATTACHMENT_FETCH_FAILED,
    ATTACHMENT_FETCH_TIMEOUT,
    ATTACHMENT_TOO_LARGE,
    AttachmentFetchError,
    ChannelAdapterUnavailable,
    ChannelBotNotFound,
    FetchedAttachment,
)
from .media import download_media as download_media_content
from .sdk_port import (
    EventCallback,
    MessageCallback,
    WeComInboundEvent,
    WeComInboundMessage,
    WeComMediaContent,
    WeComMediaDecryptError,
    WeComMediaNetworkError,
    WeComMediaRef,
    WeComMediaTimeoutError,
    WeComMediaTooLargeError,
    WeComSdkConnectionError,
    WeComSdkError,
    WeComSdkFactory,
    WeComSdkPort,
)

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF_BASE_SEC = 1.0
DEFAULT_BACKOFF_MAX_SEC = 30.0
DEFAULT_STREAM_FLUSH_INTERVAL_SEC = 0.5
# 连接存活探测间隔：SDK 不保证服务端主动关闭时回调 on_disconnected，按有界间隔兜底探测
DEFAULT_LIVENESS_INTERVAL_SEC = 1.0
# 待取件引用的存活时长。企微的媒体 URL 五分钟内有效（设计 §3.2.2），过期后取也没用；
# TTL 同时是"永远不会被取走"的那批引用的回收手段——命令消息、重复投递、空载荷兜底
# 这几条路径都不会调 fetch_attachment。
MEDIA_REF_TTL_SEC = 300.0
# 待取件引用的容量上限：TTL 之内消息量突增时仍有硬边界（按插入顺序淘汰最旧的）。
MEDIA_REF_CAPACITY = 256
TEXT_MESSAGE_TYPE = "text"
IMAGE_MESSAGE_TYPE = "image"
FILE_MESSAGE_TYPE = "file"
MIXED_MESSAGE_TYPE = "mixed"
#: 图文混排里多个文本项的连接符：直接相接会把两句话粘成一个词
MIXED_TEXT_SEPARATOR = "\n"
#: 会产出媒体引用的 msgtype（`mixed` 的图片项由 `_mixed_payload` 单独处理）
ACCEPTED_MEDIA_TYPES = frozenset({IMAGE_MESSAGE_TYPE, FILE_MESSAGE_TYPE})
#: 本适配器已"认得"的消息类型。未列出的类型不是格式错误，而是**本渠道不接收**，须走反馈路径。
ACCEPTED_MESSAGE_TYPES = frozenset({TEXT_MESSAGE_TYPE, MIXED_MESSAGE_TYPE}) | ACCEPTED_MEDIA_TYPES
_UNSUPPORTED_MESSAGE_TYPES: dict[str, UnsupportedMedia] = {"voice": "VOICE", "video": "VIDEO"}
STREAM_HEADER_KEY = "headers"
STREAM_REPLY_ID_KEY = "req_id"
CONNECTED_GAUGE = "wecom_ws_connected"
CONNECTED_GAUGE_HELP = "WeCom bot WebSocket connection state (1=CONNECTED)"

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


@dataclass(slots=True)
class _PendingMedia:
    """一条消息待取件的媒体引用 + 插入时刻（用于 TTL 驱逐）。"""

    refs: tuple[WeComMediaRef, ...]
    created_at: float


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
        self._attempt = 0
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
        self._set_state(ConnectionState.STOPPING)
        await self._release_client()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._set_state(ConnectionState.DISCONNECTED)

    async def _run(self) -> None:
        delay = self._backoff_base_sec
        while not self._stop_requested.is_set():
            self._set_state(ConnectionState.CONNECTING)
            try:
                await self._connect_once()
            except ChannelAdapterUnavailable as exc:
                self._record_failure("wecom_bot_secret_missing", exc)
                self._set_state(ConnectionState.BACKOFF)
                self._signal_first_attempt()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff_max_sec)
                continue
            except Exception as exc:
                self._record_failure("wecom_bot_connect_failed", exc)
                self._set_state(ConnectionState.BACKOFF)
                self._signal_first_attempt()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff_max_sec)
                continue
            delay = self._backoff_base_sec
            self._signal_first_attempt()
            await self._wait_for_disconnect()
            if self._stop_requested.is_set():
                return
            self._set_state(ConnectionState.BACKOFF)
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
        # 重连前先释放上一轮客户端：SDK 的心跳/接收任务只由 disconnect() 取消
        await self._release_client()
        self._attempt += 1
        secret = await self._resolve_bot_secret()
        client = self._sdk_factory(self.bot.bot_id, secret)
        self._client = client
        self._wire(client)
        self._disconnected.clear()
        await client.connect()

    async def _release_client(self) -> None:
        """断开并释放当前 SDK 客户端，等待其被取消的内部任务收尾。"""
        client, self._client = self._client, None
        if client is None:
            return
        client.disconnect()
        await asyncio.sleep(0)

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

    def _set_state(self, new_state: ConnectionState) -> None:
        """状态机唯一收口：迁移即更新连接指标并写可关联 trace 的迁移日志（不含 Secret）。"""
        previous = self._state
        self._state = new_state
        metrics.set_gauge(
            CONNECTED_GAUGE,
            1.0 if new_state is ConnectionState.CONNECTED else 0.0,
            {"bot_id": self.bot.bot_id},
            help=CONNECTED_GAUGE_HELP,
        )
        if previous is new_state:
            return
        logger.info(
            "wecom_bot_state_changed bot_id=%s from=%s to=%s attempt=%s trace_id=%s",
            self.bot.bot_id,
            previous,
            new_state,
            self._attempt,
            current_trace_id(),
        )

    def _signal_first_attempt(self) -> None:
        if not self._first_attempt.is_set():
            self._first_attempt.set()

    def _record_failure(self, event: str, error: Exception) -> None:
        self._last_error = error
        # 类名与消息都要：str(exc) 不含类名，只打 __name__ 又会丢掉「缺哪个包/为什么连不上」
        # 这类关键信息（真实事故：只落 ImportError，看不到 requires python-socks）。
        logger.warning(
            "%s bot_id=%s error=%s: %s", event, self.bot.bot_id, type(error).__name__, error
        )

    def _handle_message(self, message: WeComInboundMessage) -> None:
        self._emit(message)

    def _handle_event(self, event: WeComInboundEvent) -> None:
        logger.debug("wecom_bot_event bot_id=%s event_type=%s", self.bot.bot_id, event.event_type)

    def _handle_authenticated(self) -> None:
        if self._stop_requested.is_set():
            return
        self._set_state(ConnectionState.CONNECTED)

    def _handle_disconnected(self, reason: str) -> None:
        if self._stop_requested.is_set():
            return
        self._set_state(ConnectionState.BACKOFF)
        self._disconnected.set()
        logger.warning("wecom_bot_disconnected bot_id=%s reason=%s", self.bot.bot_id, reason)

    def _handle_error(self, error: Exception) -> None:
        self._last_error = error
        logger.warning(
            "wecom_bot_error bot_id=%s error=%s: %s", self.bot.bot_id, type(error).__name__, error
        )
        if self._stop_requested.is_set():
            return
        self._set_state(ConnectionState.BACKOFF)
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


def _media_ref(payload: Mapping[str, object]) -> WeComMediaRef | None:
    """企微的取件凭据在**同一个对象**里：`{url, aeskey?}`（设计 §3.2.2）。缺 `url` 则不可取件。"""
    url = _as_str(payload, "url")
    if not url:
        return None
    return WeComMediaRef(url=url, aes_key=_as_str(payload, "aeskey") or None)


def _payload(body: Mapping[str, object], msgtype: str) -> tuple[str, tuple[WeComMediaRef, ...]]:
    """返回 `(并入消息文本, 媒体引用)`——`mixed` 下两者可同时非空。"""
    if msgtype == TEXT_MESSAGE_TYPE:
        return _as_str(_as_mapping(body.get(TEXT_MESSAGE_TYPE)), "content"), ()
    if msgtype in ACCEPTED_MEDIA_TYPES:
        ref = _media_ref(_as_mapping(body.get(msgtype)))
        return "", (ref,) if ref is not None else ()
    if msgtype == MIXED_MESSAGE_TYPE:
        return _mixed_payload(_as_mapping(body.get(MIXED_MESSAGE_TYPE)))
    return "", ()


def _mixed_payload(mixed: Mapping[str, object]) -> tuple[str, tuple[WeComMediaRef, ...]]:
    """图文混排：`image` 项**各成一个**媒体引用；`text` 项按出现顺序并入消息文本。

    多个文本项之间用换行分隔——直接首尾相接会把两句话粘成一个词。
    """
    items = mixed.get("msg_item")
    texts: list[str] = []
    refs: list[WeComMediaRef] = []
    for item in items if isinstance(items, list) else ():
        entry = _as_mapping(item)
        item_type = _as_str(entry, "msgtype")
        if item_type == TEXT_MESSAGE_TYPE:
            content = _as_str(_as_mapping(entry.get(TEXT_MESSAGE_TYPE)), "content")
            if content:
                texts.append(content)
        elif item_type in ACCEPTED_MEDIA_TYPES:
            ref = _media_ref(_as_mapping(entry.get(item_type)))
            if ref is not None:
                refs.append(ref)
    return MIXED_TEXT_SEPARATOR.join(texts), tuple(refs)


def _unsupported_media(msgtype: str) -> UnsupportedMedia | None:
    """本渠道不接收的载荷形态（RULE-01：不得静默丢弃）。

    已知的语音/视频给具体取值，便于日后按类型改文案；其余非空且未接收的类型归 `OTHER`。
    空 `msgtype` 返回 `None`——那不是一条可归属的消息。
    """
    if msgtype in _UNSUPPORTED_MESSAGE_TYPES:
        return _UNSUPPORTED_MESSAGE_TYPES[msgtype]
    if msgtype and msgtype not in ACCEPTED_MESSAGE_TYPES:
        return "OTHER"
    return None


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
        """主动投递纯文本：官方 `aibot_send_msg` 体只支持 markdown / template_card，
        用 `text` 会被服务端以 `errcode=40008 invalid message type` 拒收（2026-09-30 真机实测）。"""
        await self._client.send_message(chat_id, {"msgtype": "markdown", "markdown": {"content": text}})

    async def reply_text(self, reply_id: str, text: str) -> None:
        """会话内文本回复：回复体只支持 stream / template_card，故用 stream 体一次收尾
        （`finish=True`）。命令为 `aibot_respond_msg`，并带**入站回调的 req_id**。"""
        await self._client.reply_stream(
            {"headers": {"req_id": reply_id}},
            uuid4().hex,
            text,
            finish=True,
        )

    async def send_stream(self, reply_id: str, stream_id: str, content: str, *, finish: bool) -> None:
        frame: dict[str, object] = {STREAM_HEADER_KEY: {STREAM_REPLY_ID_KEY: reply_id}}
        await self._client.reply_stream(frame, stream_id, content, finish=finish)

    async def download_media(self, url: str, aes_key: str | None, *, max_bytes: int) -> WeComMediaContent:
        """取件不走 SDK（`WSClient.download_file` 无字节上限、超时不可配、密钥缺失返回密文），
        只复用它的解密函数——细节见 `media` 模块文档串。"""
        return await download_media_content(url, aes_key, max_bytes=max_bytes)

    def _deliver_message(self, frame: object, callback: MessageCallback) -> None:
        message = self._to_inbound_message(frame)
        if message is not None:
            callback(message)

    def _deliver_event(self, frame: object, callback: EventCallback) -> None:
        event = self._to_inbound_event(frame)
        if event is not None:
            callback(event)

    def _to_inbound_message(self, frame: object) -> WeComInboundMessage | None:
        """按 `msgtype` 分流（设计 §3.2.2）。

        可接收的（`image` / `file` / `mixed` 内的 `image` 项）产出**渠道私有的媒体引用**；
        本渠道不接收但**结构合法**的（`voice` / `video` / 未知类型）以 `unsupported_media` 表达，
        使消息不再在渠道边界消失（RULE-01）。**结构不合法**的帧（缺 `msgid`/`from`、图片缺 `url`）
        仍返回 `None`：那不是"收不了的消息"，而是无法归属的帧。
        """
        if not isinstance(frame, Mapping):
            return None
        body = _as_mapping(frame.get("body"))
        headers = _as_mapping(frame.get(STREAM_HEADER_KEY))
        message_id = _as_str(body, "msgid")
        user_id = _as_str(_as_mapping(body.get("from")), "userid")
        if not message_id or not user_id:
            return None
        msgtype = _as_str(body, "msgtype")
        text, media = _payload(body, msgtype)
        unsupported = None if text or media else _unsupported_media(msgtype)
        if not text and not media and unsupported is None:
            return None
        return WeComInboundMessage(
            bot_id=self._bot_id,
            message_id=message_id,
            external_user_id=user_id,
            external_conversation_id=_as_str(body, "chatid") or None,
            message_type=msgtype,
            text=text,
            reply_id=_as_str(headers, STREAM_REPLY_ID_KEY),
            media=media,
            unsupported_media=unsupported,
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
        self._pending_media: dict[str, _PendingMedia] = {}
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

    @property
    def degraded_bots(self) -> dict[str, str]:
        """未 CONNECTED 的 bot → 连接状态（design §4.1 的 degraded 详情）。"""
        return {
            bot_id: connection.state.value
            for bot_id, connection in self._connections.items()
            if connection.state is not ConnectionState.CONNECTED
        }

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
        unavailable = [
            connection
            for connection in self._connections.values()
            if isinstance(connection.last_error, ChannelAdapterUnavailable)
        ]
        # 设计 §4.1：单 bot 缺失/失效 Secret 只记 degraded 并退避，不停止其他 bot；
        # 只有全部 bot 都无可用凭据（无任何可服务连接）才算整体必需条件缺失。
        if not unavailable or len(unavailable) < len(self._connections):
            return
        failure = unavailable[0].last_error
        await self.stop()
        raise ChannelAdapterUnavailable(
            f"wecom bot secret unavailable bot_id={unavailable[0].bot.bot_id}"
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
        # 该会话有入站回调的 reply_id 时，回复必须走会话内命令（`aibot_respond_msg`）：
        # 官方服务对 `aibot_send_msg`（主动发送）回会话会以 errcode=40008 拒收
        # （2026-09-30 真机复验：「绑定成功」回执即因此失败）。仅真正的主动投递
        # （无回调上下文）才用 `send_text`。
        reply_ref = self._reply_refs.get(route_key(route))
        if reply_ref is not None:
            await client.reply_text(reply_ref, message.text)
            return
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
        if message.media:
            self._remember_media(message.message_id, message.media)
        # `attachments` 此刻必为空：取件（`message.media`，渠道私有）与落盘由网关应用编排层
        # 按设计 §3.2.1 的 ②→⑥ 顺序接线后填充；适配器只负责把"来了一条什么"送达边界。
        self._events.put_nowait(
            ChannelEnvelope(
                channel="WECOM",
                bot_id=message.bot_id,
                external_user_id=message.external_user_id,
                external_conversation_id=message.external_conversation_id,
                message_id=message.message_id,
                text=message.text,
                unsupported_media=message.unsupported_media,
            )
        )

    def _remember_media(self, message_id: str, refs: tuple[WeComMediaRef, ...]) -> None:
        """记下待取件引用并**驱逐**：过期的一律丢掉，仍超容量则丢最旧的。

        不驱逐就会漏：命令消息、被去重丢弃的重复投递、空载荷兜底——这些路径都不取件，
        它们的引用如果留在表里就是纯泄漏。TTL 对齐企微 URL 的五分钟有效期。
        """
        now = asyncio.get_running_loop().time()
        self._pending_media = {
            key: item
            for key, item in self._pending_media.items()
            if now - item.created_at < MEDIA_REF_TTL_SEC
        }
        while len(self._pending_media) >= MEDIA_REF_CAPACITY:
            oldest = min(self._pending_media, key=lambda key: self._pending_media[key].created_at)
            del self._pending_media[oldest]
        self._pending_media[message_id] = _PendingMedia(refs=refs, created_at=now)

    def attachment_count(self, envelope: ChannelEnvelope) -> int:
        pending = self._pending_media.get(envelope.message_id)
        return len(pending.refs) if pending is not None else 0

    async def fetch_attachment(
        self, envelope: ChannelEnvelope, index: int, *, max_bytes: int
    ) -> FetchedAttachment:
        """取回并解密第 `index` 个附件。**凭据不出这个函数**——调用方只拿到字节与元信息。"""
        pending = self._pending_media.get(envelope.message_id)
        if pending is None or index < 0 or index >= len(pending.refs):
            # 过期/已驱逐/越界：明确失败，不静默给空字节（上层据此走 E-03 的失败反馈）
            raise WeComSdkError(
                f"wecom media ref unavailable message_id={envelope.message_id} index={index}"
            )
        ref = pending.refs[index]
        try:
            content = await self._require_client(envelope.bot_id).download_media(
                ref.url, ref.aes_key, max_bytes=max_bytes
            )
        except WeComMediaTooLargeError as exc:
            raise AttachmentFetchError(ATTACHMENT_TOO_LARGE) from exc
        except WeComMediaTimeoutError as exc:
            raise AttachmentFetchError(ATTACHMENT_FETCH_TIMEOUT) from exc
        except WeComMediaDecryptError as exc:
            raise AttachmentFetchError(ATTACHMENT_DECRYPT_FAILED) from exc
        except WeComMediaNetworkError as exc:
            raise AttachmentFetchError(ATTACHMENT_FETCH_FAILED) from exc
        return FetchedAttachment(
            data=content.data,
            media_type=content.media_type,
            filename=content.filename,
            checksum=content.checksum,
        )

    def _require_client(self, bot_id: str) -> WeComSdkPort:
        connection = self._connections.get(bot_id)
        if connection is None:
            # 快照中没有该 bot（未配置/已停用）≠ 连接暂时不可用
            raise ChannelBotNotFound(f"wecom bot connection not configured bot_id={bot_id}")
        return connection.require_client()

    def _ensure_started(self) -> None:
        if not self._started:
            raise ChannelAdapterUnavailable("wecom adapter is not started")

    def _enabled_bots(self) -> tuple[BotSnapshotItem, ...]:
        return tuple(bot for bot in self._bots if bot.enabled)
