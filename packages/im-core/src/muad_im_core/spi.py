"""Connector/Gateway 可实现的渠道 SPI；核心不 import 任何 SDK。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from muad_contracts import RunStatus
from muad_contracts.im import ChannelCapabilities, DeliveryCommand, InboundEvent
from muad_contracts.webchat import WebchatExecutionStatus


@dataclass(frozen=True, slots=True)
class SyncPrincipal:
    """入口门控**已经验证过**的提交上下文（SYNC 核心不回源 Console 解析）。

    核心只把它原样交给装配根注入的受理端口：主体、入口解析结论与幂等身份都来自入口
    （WebChat 的 `ChatIdentity` + 已核属的入口），所以受理路径里没有第二次身份解析。
    """

    tenant_id: str
    platform_user_id: UUID
    #: 本次入口已解析且已授权的 Agent（授权判定在入口完成）。
    agent_id: UUID
    #: 调用方给出的幂等身份（WebChat 的 `Idempotency-Key`）：同键同指纹重放原 Run。
    idempotency_key: str
    #: 先上传后提交的暂存引用（网页入口；其他渠道为空）。
    upload_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncAdmission:
    """WebChat SYNC 受理结果：调用方据此返回 202，**不再**为回执回读 Run。

    状态与阶段投影来自 Runtime 的受理事务（新 Run）或订单重放时的 Run 行（幂等重放），
    两者在受理响应头里一次带回。
    """

    run_id: UUID
    conversation_id: UUID
    status: RunStatus
    execution_status: WebchatExecutionStatus


@dataclass(frozen=True, slots=True)
class AsyncAdmission:
    """IM ASYNC 受理结果：入站已持久化/入队后返回。"""

    accepted: bool
    duplicate: bool = False


class ChannelAdapter(Protocol):
    """新 IM 只需实现此协议；渠道 SDK/协议细节不得进入核心域。"""

    @property
    def channel(self) -> str: ...

    def capabilities(self) -> ChannelCapabilities: ...

    def normalize(self, raw: object) -> InboundEvent: ...

    async def send(self, command: DeliveryCommand) -> None: ...
