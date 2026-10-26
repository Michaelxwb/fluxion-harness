"""本地真实 HTTP 渠道探针适配器（Connector 验收/联调用）。

设计允许外部渠道用本地 HTTP 探针替代第三方实网：投递请求以真实 HTTP 发出，
探针记录请求并返回结果，便于在无外网环境验证投递链路与去重语义。

它是 Connector 的**通道适配器表面**（`ConnectorChannelPort`）：只负责"发出去"与连接存在性，
不产生入站事件（入站由测试直接调用探针端点的反向路径驱动）。渠道形状（HTTP 载荷）留在本文件，
核心域与 Gateway 一步都不碰。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from enum import StrEnum

import httpx
from muad_contracts import (
    AttachmentRef,
    BotSnapshotItem,
    ChannelEnvelope,
    DeliveryMessage,
    DeliveryRouteInput,
)
from muad_im_gateway.channels.base import ChannelAdapterUnavailable

from .wecom.sdk_port import WeComInboundMessage


class ProbeConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


class HttpProbeChannelAdapter:
    """HTTP 探针通道：`CHANNEL_PROBE_URL` 指向记录投递的本地端点。"""

    delivers_locally = True

    def __init__(self, url: str, *, name: str = "WECOM", timeout_sec: float = 10.0) -> None:
        self.name = name
        self._url = url
        self._timeout_sec = timeout_sec
        self._started = False
        self._bots: tuple[BotSnapshotItem, ...] = ()
        self._raw_sink: Callable[[WeComInboundMessage], None] | None = None

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        """探针不产生入站事件：保持与 ChannelAdapter 协议一致的异步迭代器。"""
        return
        yield ChannelEnvelope(  # pragma: no cover - 仅为声明异步生成器类型
            channel=self.name, bot_id="", external_user_id="", message_id=""
        )

    def set_raw_sink(self, sink: Callable[[WeComInboundMessage], None] | None) -> None:
        """探针没有渠道原始消息；保留装配点（runtime 统一绑定）。"""
        self._raw_sink = sink

    async def drop_bot(self, bot_id: str) -> None:
        self._bots = tuple(item for item in self._bots if item.bot_id != bot_id)

    def has_bot(self, bot_id: str) -> bool:
        return any(item.bot_id == bot_id and item.enabled for item in self._bots)

    @property
    def connection_states(self) -> dict[str, ProbeConnectionState]:
        if not self._started:
            return {}
        return {item.bot_id: ProbeConnectionState.CONNECTED for item in self._bots if item.enabled}

    def healthy(self) -> bool:
        return self._started

    async def apply_snapshot(self, bots: Sequence[BotSnapshotItem]) -> None:
        """与 WeComAdapter 同构：探针不维护连接，仅接受本通道的启用条目。"""
        self._bots = tuple(item for item in bots if item.channel == self.name and item.enabled)

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        await self.send_active(route, message)

    async def send_active(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        await self._post(
            {
                "bot_id": route.bot_id,
                "external_user_id": route.external_user_id,
                "external_conversation_id": route.external_conversation_id,
                "kind": "active",
                "text": message.text,
            }
        )

    async def send_reply_text(
        self, route: DeliveryRouteInput, reply_ref: str, text: str
    ) -> None:
        await self._post(self._route_payload(route) | {"kind": "reply_text", "reply_ref": reply_ref,
                                                        "text": text})

    async def send_reply_stream(
        self,
        route: DeliveryRouteInput,
        reply_ref: str,
        stream_id: str,
        text: str,
        *,
        finish: bool,
    ) -> None:
        await self._post(
            self._route_payload(route)
            | {
                "kind": "reply_stream",
                "reply_ref": reply_ref,
                "stream_id": stream_id,
                "text": text,
                "finish": finish,
            }
        )

    async def send_status(
        self,
        route: DeliveryRouteInput,
        reply_ref: str,
        stream_id: str,
        status: str,
        body: str,
    ) -> None:
        await self.send_reply_stream(route, reply_ref, stream_id, status, finish=False)

    async def deliver_artifact_with_target(
        self,
        route: DeliveryRouteInput,
        artifact: AttachmentRef,
        *,
        tenant_id: str,
        run_id: str | None,
        reply_ref: str | None,
    ) -> tuple[str, str | None]:
        """探针接受产物元数据（真实 HTTP 往返），不声明降级链接。"""
        await self._post(
            self._route_payload(route)
            | {
                "kind": "artifact",
                "reply_ref": reply_ref,
                "run_id": run_id,
                "tenant_id": tenant_id,
                "artifact_id": str(artifact.artifact_id),
                "storage_key": artifact.storage_key,
            }
        )
        return "DELIVERED", None

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        await self.send_reply_stream(
            route, "", "probe-stream", "".join([chunk async for chunk in chunks]), finish=True
        )

    def route_key(self, route: DeliveryRouteInput) -> str:
        return f"{route.bot_id}:{route.external_user_id}"

    def _route_payload(self, route: DeliveryRouteInput) -> dict[str, object]:
        return {
            "bot_id": route.bot_id,
            "external_user_id": route.external_user_id,
            "external_conversation_id": route.external_conversation_id,
        }

    async def _post(self, payload: dict[str, object]) -> None:
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            try:
                response = await client.post(self._url, json=payload)
            except httpx.HTTPError as exc:
                raise ChannelAdapterUnavailable(f"probe unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ChannelAdapterUnavailable(f"probe rejected: {response.status_code}")


__all__ = ["HttpProbeChannelAdapter", "ProbeConnectionState"]
