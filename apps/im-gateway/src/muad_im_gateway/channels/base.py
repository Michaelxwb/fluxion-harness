from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from muad_contracts import (
    AttachmentRef,
    ChannelEnvelope,
    DeliveryAuditOutcome,
    DeliveryMessage,
    DeliveryRouteInput,
)

logger = logging.getLogger(__name__)


class ChannelAdapterUnavailable(Exception):
    pass


class ChannelBotNotFound(ChannelAdapterUnavailable):
    """bot 未配置/已停用（快照里没有该 bot）：与"暂时不可用"区分，投递侧映射 BOT_NOT_FOUND。"""


class ChannelRegistryError(Exception):
    pass


class DuplicateChannelAdapterError(ChannelRegistryError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"channel adapter already registered: {name}")


class UnknownChannelAdapterError(ChannelRegistryError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"channel adapter not registered: {name}")


class ChannelAdapter(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def iter_events(self) -> AsyncIterator[ChannelEnvelope]: ...
    async def send(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None: ...
    async def stream(self, route: DeliveryRouteInput, chunks: AsyncIterator[str]) -> None: ...


class ChannelReplySession(Protocol):
    """一次入站消息的回复会话（可选能力，设计 SESSION-01）。

    与会话级的「最新回调」无关：本会话只服务**打开它的那条消息**，所以同会话后来的入站
    消息不会把它的回复目标顶掉。
    """

    def bind_run(self, run_id: str) -> None:
        """把本会话绑到发起它的 Run：产物按 `run:{run_id}:{artifact_id}` 投递时，
        适配器据此找回**本条消息**的回调，而不是本路由最新那条。"""
        ...

    async def update_status(self, text: str) -> None: ...

    async def stream(self, text: str) -> None: ...

    async def send(self, text: str) -> None: ...

    async def reply_once(self, text: str) -> None:
        """发一条**独立、已收尾**的文本（命令回执 / 附件回执 / 错误文案）。

        与 `send` 的区别是它**不参与流式累积**：这类文本没有后续，客户端应当一次收到完整
        内容。没有这个能力就等于每条回执都被追加上去、且要等一次 finalize —— 回执、错误
        文案与正文会粘成一条不断增长的流。
        """
        ...

    async def finish(self) -> None: ...


@runtime_checkable
class ReplySessionFactory(Protocol):
    def open_reply(self, route: DeliveryRouteInput, message_id: str) -> ChannelReplySession: ...


#: 附件的原因码词汇表——**渠道中立**：渠道层（可能抛）与应用层（映射文案 + 写审计）共用同一套。
#: 单文件超限与门控实检里的 `ATTACHMENT_TOO_LARGE` 是**同一个码**：超限这件事只有一种说法。
ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
ATTACHMENT_FETCH_TIMEOUT = "ATTACHMENT_FETCH_TIMEOUT"
ATTACHMENT_FETCH_FAILED = "ATTACHMENT_FETCH_FAILED"
ATTACHMENT_DECRYPT_FAILED = "ATTACHMENT_DECRYPT_FAILED"


class AttachmentFetchError(Exception):
    """取件失败（渠道中立）。

    渠道适配器**必须**把自己的私有异常翻译成这个类型——否则应用层就得 import 具体渠道的异常，
    "换通道只写适配器"就不成立了（RULE-07 / S-07）。`code` 同时用于用户可见反馈与审计原因码。
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchedAttachment:
    """渠道取回并**已解密**的媒体内容。

    刻意与契约 `AttachmentRef` 不同：这里已经有字节，但**还没有产物键**——落盘属应用编排层。
    """

    data: bytes
    media_type: str
    filename: str | None
    checksum: str


@runtime_checkable
class AttachmentSource(Protocol):
    """可选能力：本通道能否按 envelope 取回附件字节（AD-8 的跨层接缝）。

    核心域只给 envelope、下标与上限，**拿回的是已解密字节与元信息**——`url`/`aes_key` 这类
    渠道私有形状一步都不出去；新通道只要实现这两个方法，门控/落盘/契约那一侧一行不用改。
    """

    def attachment_count(self, envelope: ChannelEnvelope) -> int: ...

    async def fetch_attachment(
        self, envelope: ChannelEnvelope, index: int, *, max_bytes: int
    ) -> FetchedAttachment: ...


#: 交付结局（渠道中立，与契约 `DeliveryAuditOutcome` 同口径）。
#: `DEGRADED` **不是失败**：用户确实收到了东西（一条签名取件链接），只是形态与预期不同。
#: 显式标注成契约的 Literal：这样 `ArtifactDeliveryOutcome(outcome=...)` 才过得了类型检查，
#: 也保证这两处取值域与审计表那一列**永远一致**。
ARTIFACT_DELIVERED: DeliveryAuditOutcome = "DELIVERED"
ARTIFACT_DEGRADED: DeliveryAuditOutcome = "DEGRADED"
#: 交付失败的原因码（渠道中立）。与消息目录里同名码一致——**一处定义**。
ARTIFACT_DELIVERY_FAILED = "ARTIFACT_DELIVERY_FAILED"


class ArtifactDeliveryError(Exception):
    """产物发送失败（渠道中立）。

    与 `AttachmentFetchError` 同口径：适配器**必须**把自己的私有异常翻译成这个类型，
    否则应用层就得 import 具体渠道的异常，"换通道只写适配器"立刻不成立。
    `code` 同时用于工具结论文案与交付审计的原因码。
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@runtime_checkable
class ActiveTextDelivery(Protocol):
    """可选能力：**不借任何入站回调**，直接向会话主动发送一条文本。

    `ReplySession` 失效（回调过期、或同会话后来的消息把路由级回调顶掉）时，Run 的终态文本
    走这条缝：适配器自己决定往哪发（企微 = `aibot_send_msg` 发给会话）。没实现 = 本通道
    不能主动投递 —— 调用方必须**显式失败**，不得把文本悄悄塞回"最新回调"或假装已送达。
    """

    async def send_active(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None: ...


@dataclass(frozen=True, slots=True)
class ArtifactDeliveryOutcome:
    """一次产物交付的结局。

    `fallback_url` 只在降级时有值——那是**用户实际收到的东西**，不是内部记号；
    把 `DEGRADED` 当失败会让模型对用户说"没发出去"，那是谎报（RULE-03）。
    """

    #: 与契约 `DeliveryAuditOutcome` **同口径**：交付审计那一列存的就是它，两边取值域必须一致
    outcome: DeliveryAuditOutcome
    fallback_url: str | None = None
    reason_code: str = ""


@runtime_checkable
class ArtifactLinkIssuer(Protocol):
    """取件直链的签发口（TASK-008 的能力）。

    适配器在"**不能直发**"时用它换一条终端用户点得开的链接。端口定义在渠道层、实现在
    `application`（`ConsoleClient`）：适配器只认这个动词，**不认识签发端点的形状**——
    与 `AttachmentSource`／`OutboundArtifactDelivery` 同一条接缝（`RULE-im-002`）。

    返回 `None` = 签不出来（产物已被清掉 / Console 不通）。适配器据此**显式失败**，
    绝不自己拼一条看起来像链接的串——那会让用户点开一个 404，比报错更糟。
    """

    async def issue_fetch_link(self, artifact_id: UUID, *, tenant_id: str) -> str | None: ...


@runtime_checkable
class OutboundArtifactDelivery(Protocol):
    """可选能力：本通道能否把**产物发出去**（AD-8 的对称接缝，与 `AttachmentSource` 同构）。

    适配器拿到的是**渠道中立的引用**（`AttachmentRef`：存储键 + 元信息 + `artifact_id`），
    自己按 key 去共享 store 读字节、自己决定怎么发（直发文件/图片，或降级为取件链接）。
    核心域与网关应用层**一步都不碰渠道的发送体**——`RULE-im-002` 要求的那条接缝就在这里。

    **没实现这个协议 = 本通道不会发产物**：调用方必须显式失败，不得静默丢（RULE-01 的精神）。
    """

    def route_key(self, route: DeliveryRouteInput) -> str:
        """把一条路由压成**可读的不透明串**，供交付审计使用（设计 §3.3）。

        **由适配器产出**，因为它的形状是渠道私有的（企微 = `{bot_id}:{external_user_id}`，
        未来 web chat = `session:{id}`）。审计表只把它当不透明串存 —— 接新渠道时那一列
        仍然填得出真值，而"拆成 bot_id/external_user_id 两列"会当场填不出。
        """
        ...

    async def deliver_artifact(
        self,
        route: DeliveryRouteInput,
        artifact: AttachmentRef,
        *,
        tenant_id: str,
        run_id: str | None = None,
    ) -> ArtifactDeliveryOutcome:
        """`tenant_id` 是**签发降级链接**要用的作用域，不是渠道形状：取件端点按租户做归属校验，
        少了它，降级这条路要么签不出来、要么只能烧死一个租户。

        `run_id` 来自交付键（`run:{run_id}:{artifact_id}`）：**同一个路由上可能有多条入站
        消息**，按它才能把产物回到**起这个 Run 的那条消息**的回调上；键里没有 run（后台任务
        投递）时为 `None`，适配器退回本路由的最新回调。

        设计 §3.6 写的是"核心域只给**产物引用 + 路由**"——那句话要挡的是**渠道发送体**
        （url / aes_key / media_id 之类）不得上行，租户与 Run 都不在其中。
        """
        ...


@runtime_checkable
class AdapterDegradation(Protocol):
    """连接管理器可报告未 CONNECTED 的 bot（`/readyz` 的 degraded 详情来源）。"""

    @property
    def degraded_bots(self) -> Mapping[str, str]: ...


@runtime_checkable
class StreamFinalizer(Protocol):
    async def finish_stream(self, route: DeliveryRouteInput) -> None: ...


def adapter_degraded(adapter: ChannelAdapter) -> Mapping[str, str]:
    if isinstance(adapter, AdapterDegradation):
        return adapter.degraded_bots
    return {}


class ChannelRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._started: set[str] = set()

    def register(self, adapter: ChannelAdapter) -> None:
        if adapter.name in self._adapters:
            raise DuplicateChannelAdapterError(adapter.name)
        self._adapters[adapter.name] = adapter

    def get(self, channel: str) -> ChannelAdapter:
        adapter = self._adapters.get(channel)
        if adapter is None:
            raise UnknownChannelAdapterError(channel)
        return adapter

    def names(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    @property
    def adapter_states(self) -> dict[str, bool]:
        return {name: name in self._started for name in self._adapters}

    @property
    def started_adapters(self) -> tuple[ChannelAdapter, ...]:
        return tuple(self._adapters[name] for name in self._started)

    @property
    def degraded_bots(self) -> dict[str, str]:
        """未 CONNECTED 的 bot → 状态：只用于 `/readyz` 的 degraded 标记，不影响就绪（设计 §4.1）。"""
        degraded: dict[str, str] = {}
        for adapter in self.started_adapters:
            degraded.update(adapter_degraded(adapter))
        return degraded

    async def start_all(self) -> None:
        for name, adapter in self._adapters.items():
            try:
                await adapter.start()
            except ChannelAdapterUnavailable as exc:
                logger.warning("channel_adapter_unavailable name=%s error=%s", name, exc)
                continue
            self._started.add(name)

    async def stop_all(self) -> None:
        for name in tuple(self._started):
            adapter = self._adapters[name]
            try:
                await adapter.stop()
            except ChannelAdapterUnavailable as exc:
                logger.warning("channel_adapter_stop_unavailable name=%s error=%s", name, exc)
            finally:
                self._started.discard(name)
