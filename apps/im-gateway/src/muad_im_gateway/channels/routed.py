"""远程 Connector 通道的**请求面**适配器：只路由，不持连接、不发 WS、不读 secret。

设计 §3.2：企微等有状态通道的连接所有权在 Connector；Gateway 只是同一个逻辑
`im-gateway` 单元的 web 角色。于是 Gateway 也需要一个同名通道适配器，但它的能力被刻意
缩到最小：

- 入站：`iter_events()` 恒空 —— 入站事实经 `im.inbound:{shard}` 由 Gateway 的消费组读入；
- 出站：所有文本/增量/收尾都写成 `im.outbound:{tenant}:{bot}` 命令，由持有 bot lease 的
  Connector 执行；Gateway **不**声明 `LocalDeliverySurface`（`delivers_locally` 一律不写，
  否则 `isinstance(adapter, LocalDeliverySurface)` 会把它当成"本进程直发"）；
- 回复目标：适配器只带**入站消息身份**（`message_id`）与不透明的 `stream_id`；reply_ref
  由 Connector 按自己的 reply store 解析，渠道形状一步都不上行。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol

from muad_contracts import ChannelEnvelope, DeliveryMessage, DeliveryRouteInput
from muad_contracts.im import DeliveryKind

from .base import ChannelAdapterUnavailable

logger = logging.getLogger(__name__)

#: 状态帧的有界等待（与旧 WS 会话同口径：状态可丢，正文/收尾必须确认）。
STATUS_WAIT_SEC = 5.0
#: 终帧的有界重试：一次瞬时失败不该让回复永远停在不收尾状态。
FINISH_ATTEMPTS = 3
FINISH_RETRY_DELAY_SEC = 0.5


class ConnectorCommandSink(Protocol):
    """出站命令端口（实现见 `application.connector_outbound`）。

    适配器只认这四个动词 + 一次有界等待；`DeliveryStreams`/Redis 的形状不进 channels 层。
    """

    async def enqueue(
        self,
        *,
        channel: str,
        route: DeliveryRouteInput,
        kind: DeliveryKind,
        text: str = "",
        message_id: str | None = None,
        run_id: str | None = None,
        stream_id: str | None = None,
        status: bool = False,
    ) -> uuid.UUID: ...

    async def await_result(
        self, command_id: uuid.UUID, *, timeout_sec: float
    ) -> dict[str, Any] | None: ...


def parse_routed_channels(raw: str) -> tuple[str, ...]:
    """解析 `GATEWAY_ROUTED_CHANNELS`（逗号分隔）；空 = 本部署没有远程通道。"""
    names: list[str] = []
    for item in raw.split(","):
        name = item.strip().upper()
        if name and name not in names:
            names.append(name)
    return tuple(names)


class OutboundReplySession:
    """一次入站消息的回复会话：status/正文/收尾 → `im.outbound` 命令。

    与 Connector 侧 `ReplySession` 的分工：**写入顺序与累积**由消费端（Connector）按
    Stream 顺序完成；这里只保证"同一条消息的命令带同一个 message_id/stream_id"。
    """

    def __init__(
        self,
        sink: ConnectorCommandSink,
        route: DeliveryRouteInput,
        *,
        message_id: str,
        stream_id: str | None = None,
        wait_sec: float = STATUS_WAIT_SEC,
    ) -> None:
        self._sink = sink
        self._route = route
        self._message_id = message_id
        self._stream_id = stream_id or uuid.uuid4().hex
        self._wait_sec = wait_sec
        self._run_id: str | None = None
        self._sent = False
        self._closed = False
        self._closing = False

    def bind_run(self, run_id: str) -> None:
        self._run_id = run_id

    async def update_status(self, text: str) -> None:
        """状态刷新是**可丢的**：通道慢或等待超时时不能把正文顶在后面。"""
        if self._closed or self._closing:
            return
        try:
            command_id = await self._sink.enqueue(
                channel=self._route.channel,
                route=self._route,
                kind=DeliveryKind.DELTA,
                text=text,
                message_id=self._message_id,
                run_id=self._run_id,
                stream_id=self._stream_id,
                status=True,
            )
        except Exception as exc:  # noqa: BLE001 - 状态帧失败不改变回复生命周期
            logger.warning("routed_status_enqueue_failed channel=%s error=%s",
                           self._route.channel, type(exc).__name__)
            return
        try:
            await self._sink.await_result(command_id, timeout_sec=self._wait_sec)
        except Exception as exc:  # noqa: BLE001 - 同上：下一个 tick 带最新阶段重来
            logger.warning("routed_status_wait_failed channel=%s error=%s",
                           self._route.channel, type(exc).__name__)

    async def stream(self, text: str) -> None:
        if self._closed or self._closing or not text:
            return
        await self._enqueue_body(text)
        self._sent = True

    async def send(self, text: str) -> None:
        if self._closed:
            # 收尾后再来的正文不能追加进已关闭的流：退化成一条独立文本（不静默丢）。
            await self.reply_once(text)
            return
        if not text:
            return
        await self._enqueue_body(text)
        self._sent = True

    async def reply_once(self, text: str) -> None:
        if not text:
            return
        try:
            await self._sink.enqueue(
                channel=self._route.channel,
                route=self._route,
                kind=DeliveryKind.TEXT,
                text=text,
                message_id=self._message_id,
                run_id=self._run_id,
                stream_id=self._stream_id,
            )
        except Exception as exc:  # noqa: BLE001 - 统一翻译成渠道不可用
            raise ChannelAdapterUnavailable("reply text enqueue failed") from exc

    async def finish(self) -> None:
        """收尾：入队 TERMINAL 并**有界等待 Connector 的真实送达结论**。"""
        if self._closed or self._closing:
            return
        if not self._sent:
            # 本轮没有正文：`finish` 的语义就是"收尾"（移除占位），无需发帧。
            self._closed = True
            return
        self._closing = True
        try:
            for attempt in range(FINISH_ATTEMPTS):
                try:
                    command_id = await self._sink.enqueue(
                        channel=self._route.channel,
                        route=self._route,
                        kind=DeliveryKind.TERMINAL,
                        message_id=self._message_id,
                        run_id=self._run_id,
                        stream_id=self._stream_id,
                    )
                    result = await self._sink.await_result(
                        command_id, timeout_sec=self._wait_sec
                    )
                except Exception as exc:  # noqa: BLE001 - 重试后仍失败则显式报不可用
                    if attempt + 1 >= FINISH_ATTEMPTS:
                        logger.error(
                            "routed_finish_failed channel=%s attempts=%s error=%s",
                            self._route.channel,
                            attempt + 1,
                            type(exc).__name__,
                        )
                        raise ChannelAdapterUnavailable("connector finish failed") from exc
                    await asyncio.sleep(FINISH_RETRY_DELAY_SEC)
                    continue
                if result is None or str(result.get("status")) not in (
                    "DELIVERED",
                    "DEGRADED",
                ):
                    if attempt + 1 >= FINISH_ATTEMPTS:
                        raise ChannelAdapterUnavailable("connector finish not confirmed")
                    await asyncio.sleep(FINISH_RETRY_DELAY_SEC)
                    continue
                self._closed = True
                return
        finally:
            self._closing = False

    async def _enqueue_body(self, text: str) -> None:
        try:
            await self._sink.enqueue(
                channel=self._route.channel,
                route=self._route,
                kind=DeliveryKind.DELTA,
                text=text,
                message_id=self._message_id,
                run_id=self._run_id,
                stream_id=self._stream_id,
            )
        except Exception as exc:  # noqa: BLE001 - 统一翻译成渠道不可用（调用方据此降级）
            raise ChannelAdapterUnavailable("connector delta enqueue failed") from exc


class RoutedChannelAdapter:
    """远程通道的请求面适配器：入站恒空，出站全部交给 Connector 命令流。"""

    def __init__(self, name: str, *, sink: ConnectorCommandSink | None = None) -> None:
        if not name.strip():
            raise ValueError("routed channel name must not be empty")
        self.name = name
        self._sink = sink

    async def start(self) -> None:
        """无连接可建：远程通道的长连接在 Connector。"""

    async def stop(self) -> None:
        """无连接可停。"""

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        return
        yield ChannelEnvelope(  # pragma: no cover - 仅为声明异步生成器类型
            channel=self.name, bot_id="", external_user_id="", message_id=""
        )

    def open_reply(
        self, route: DeliveryRouteInput, message_id: str
    ) -> OutboundReplySession:
        return OutboundReplySession(self._require_sink(), route, message_id=message_id)

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        await self._enqueue_text(route, message.text)

    async def send_active(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        """主动投递：同样只是入队；Connector 按 route 解析目标或主动发送。"""
        await self._enqueue_text(route, message.text)

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        text = "".join([chunk async for chunk in chunks])
        if not text:
            return
        try:
            await self._require_sink().enqueue(
                channel=self.name, route=route, kind=DeliveryKind.DELTA, text=text
            )
        except Exception as exc:  # noqa: BLE001
            raise ChannelAdapterUnavailable("connector delta enqueue failed") from exc

    def route_key(self, route: DeliveryRouteInput) -> str:
        """审计用的路由串：形状由通道决定，这里只给不透明串（不参与归属判断）。"""
        return f"{route.bot_id}:{route.external_user_id}"

    async def _enqueue_text(self, route: DeliveryRouteInput, text: str) -> None:
        if not text:
            return
        try:
            await self._require_sink().enqueue(
                channel=self.name, route=route, kind=DeliveryKind.TEXT, text=text
            )
        except Exception as exc:  # noqa: BLE001
            raise ChannelAdapterUnavailable("connector text enqueue failed") from exc

    def _require_sink(self) -> ConnectorCommandSink:
        if self._sink is None:
            raise ChannelAdapterUnavailable("connector command sink is not wired")
        return self._sink


__all__ = [
    "ConnectorCommandSink",
    "OutboundReplySession",
    "RoutedChannelAdapter",
    "parse_routed_channels",
]
