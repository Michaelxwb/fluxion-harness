"""WEBCHAT 标准通道适配器（TASK-047 / TASK-010；设计 §3.2 架构与执行边界、§3.3 数据设计）。

WEBCHAT 是**稳定入口账号**：用户经 Gateway 自己的 HTTP 提交消息、经 SSE 订阅结果，
所以本适配器与企微完全同构地接入既有通道接口，但没有第三方长连接，也不需要任何 secret。

- **入站是 SYNC**（design §3.2 WebChat SYNC 路径）：HTTP 提交经统一消息核心
  （`MessageGateway.admit_sync`）在请求内直达 Runtime，受理裁决就是 202 —— 本适配器
  **没有**进程内入站队列；`iter_events()` 保持协议形状但恒为空（与通用 webhook 同形）。
  历史上的 `submit()`/队列/消费循环已随 SYNC 收口移除（TASK-010）。
- **出站**：本通道没有外部推送目标（终端用户靠订阅面拉取）。文本/增量交给装配时注入的
  `WebchatDeliverySink`（TASK-052/061 的 HTTP/SSE 面同形）；**未装配即显式失败**，绝不静默丢。
- **快照**：只认本通道的条目（`apply_snapshot`）；企微适配器同理按实际 channel 过滤，
  WEBCHAT 条目一步都进不了企微的长连接/secret 路径。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from muad_contracts import (
    BotSnapshotItem,
    ChannelEnvelope,
    ChannelName,
    DeliveryMessage,
    DeliveryRouteInput,
)

from .base import ChannelAdapterUnavailable


@runtime_checkable
class WebchatDeliverySink(Protocol):
    """WEBCHAT 出站落点：把**渠道中立**的 route/message 交给本通道的订阅面。

    与 `ArtifactLinkIssuer` 同一条接缝：适配器只认这个动词，不认识订阅面的形状
    （端点、游标、分片都在应用层，`RULE-im-002`）。
    """

    async def deliver(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None: ...

    async def deliver_stream(
        self, route: DeliveryRouteInput, chunks: AsyncIterator[str]
    ) -> None: ...


class WebchatChannelAdapter:
    """标准 WEBCHAT 适配器：无长连接、无 secret，入站 SYNC、出站映射到统一封套。"""

    name: ChannelName = "WEBCHAT"
    #: 本通道出站由 Gateway 自己的持久入箱/SSE 面完成（`LocalDeliverySurface`）。
    delivers_locally = True

    def __init__(self, *, sink: WebchatDeliverySink | None = None) -> None:
        self._sink = sink
        self._entries: tuple[BotSnapshotItem, ...] = ()

    @property
    def channel(self) -> ChannelName:
        """统一消息核心的通道身份（与 `name` 同源；核心只做通道一致性校验）。"""
        return self.name

    @property
    def enabled_entries(self) -> tuple[BotSnapshotItem, ...]:
        """已启用的本通道入口（只含 WEBCHAT 条目；稳定入口不需要 secret）。"""
        return self._entries

    async def start(self) -> None:
        """无第三方连接：SYNC 入口不依赖启动期状态（缺 secret 不影响本通道）。"""

    async def stop(self) -> None:
        """无长连接/队列可停：保持协议形状，让注册表的启停路径对每通道同形。"""

    async def apply_snapshot(self, items: Sequence[BotSnapshotItem]) -> None:
        """只认**本通道**的条目：别人的 bot 不进本适配器的缓存（通道差异只活在这里）。"""
        self._entries = tuple(
            item for item in items if item.enabled and item.channel == self.name
        )

    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]:
        """本通道的入站来自 HTTP（SYNC 核心），不经过 `iter_events`：恒为空。"""
        return self._drain()

    async def _drain(self) -> AsyncIterator[ChannelEnvelope]:
        return
        yield ChannelEnvelope(  # pragma: no cover - 仅为声明异步生成器类型
            channel=self.name, bot_id="", external_user_id="", message_id=""
        )

    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None:
        await self._require_sink().deliver(route, message)

    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None:
        await self._require_sink().deliver_stream(route, chunks)

    def explicit_conversation_id(self, envelope: ChannelEnvelope) -> UUID | None:
        """HTTP 提交带**显式会话**（RULE-10：不依赖隐式最新指针）。

        本通道的入站事件**永远**带会话（API-06 形状）：若有一条事件经 Stream 到达（当前
        SYNC 路径不经过），缺失或坏值都**显式失败** —— 悄悄降级成"按入口指针选会话"会把
        消息写进另一条会话，那正是多历史会话串写的成因。异常只带稳定文案，不回显原值。
        """
        raw = envelope.external_conversation_id
        if not raw:
            raise ChannelAdapterUnavailable("webchat envelope carries no explicit conversation id")
        try:
            return UUID(raw)
        except ValueError as exc:
            raise ChannelAdapterUnavailable(
                "webchat envelope carries a malformed conversation id"
            ) from exc

    def _require_sink(self) -> WebchatDeliverySink:
        if self._sink is None:
            # 未装配订阅面：显式失败，绝不把文本悄悄丢掉或假装已送达。
            raise ChannelAdapterUnavailable("webchat delivery sink is not wired")
        return self._sink
