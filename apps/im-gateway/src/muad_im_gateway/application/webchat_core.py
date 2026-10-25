"""WebChat 在统一消息内核里的 SYNC 入口（API-08；design §3.2 / §3.4）。

职责与边界：

- **入口已验证主体**：`SyncPrincipal` 来自 `ChatIdentityDep`（Console 已核验的 CHAT 主体）
  与入口解析（已授权的 Agent）——本模块**不再**回源 Console 解析身份；受理端口只把
  渠道中立事件映射成 Runtime 的 `RunRequest`。
- **SYNC 不进队列**：网页提交在请求内等到 Runtime 的受理裁决（首帧）就返回 202；
  IM 的 ASYNC 路径由 Connector/Webhook 的入站 Stream 消费，不在本模块。
- **受理投影一次带回**：`SyncAdmission` 直接携带 Run 状态与阶段投影，调用方返回 202
  时不需要再 `get_run`（design §3.5.1 NFR-PERF-09）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import ChannelName, RunRequest
from muad_contracts.im import InboundEvent
from muad_contracts.runtime import ChannelContext, MessageInput
from muad_im_core import (
    MessageGateway,
    MessageGatewayPorts,
    SyncAdmission,
    SyncPrincipal,
)

from .runtime_client import RunAdmission

logger = logging.getLogger(__name__)

#: 本通道的公开入口词表（与 `BotSnapshotItem.channel` 同源）。
WEBCHAT_CHANNEL: ChannelName = "WEBCHAT"


class SyncRunOwner(Protocol):
    """SYNC 受理只要 Owner 的一个动词（Runtime 是唯一实现；结构替身同形）。"""

    async def submit_run(
        self, request: RunRequest, *, tenant_id: str, idempotency_key: str
    ) -> RunAdmission: ...


@dataclass(frozen=True, slots=True)
class WebchatChannelIdentity:
    """统一核心的通道身份：与注册表里的 `WebchatChannelAdapter.name` 同值。"""

    channel: ChannelName = WEBCHAT_CHANNEL


WEBCHAT_CHANNEL_IDENTITY = WebchatChannelIdentity()


def _run_request(event: InboundEvent, principal: SyncPrincipal) -> RunRequest:
    """渠道中立事件 + 已验证主体 → Runtime 提交契约（显式会话、暂存上传原样保留）。"""
    if event.external_conversation_id is None:
        # WebChat 提交**永远**带显式会话（API-06 形状）：缺失是调用方错误，不猜最新指针。
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return RunRequest(
        agent_id=principal.agent_id,
        platform_user_id=principal.platform_user_id,
        conversation_id=UUID(event.external_conversation_id),
        channel=ChannelContext(
            type=WEBCHAT_CHANNEL,
            bot_id=event.bot_id,
            external_user_id=event.external_user_id,
            external_conversation_id=event.external_conversation_id,
        ),
        message=MessageInput(
            id=event.external_message_id,
            type="attachment" if principal.upload_ids else "text",
            text=event.text,
            upload_ids=list(principal.upload_ids),
        ),
    )


def build_webchat_message_gateway(owner: SyncRunOwner) -> MessageGateway:
    """装配 WebChat SYNC 端口：核心做通道校验，端口只做事件→提交契约的映射。"""

    async def admit_sync(event: InboundEvent, principal: SyncPrincipal) -> SyncAdmission:
        request = _run_request(event, principal)
        admission = await owner.submit_run(
            request,
            tenant_id=principal.tenant_id,
            idempotency_key=principal.idempotency_key,
        )
        if admission.conversation_id != request.conversation_id:
            # 受理的 Run 必须落在请求的显式会话上；不一致是坏裁决，显式失败不糊过去。
            logger.warning("webchat_admission_conversation_mismatch")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return SyncAdmission(
            run_id=admission.run_id,
            conversation_id=admission.conversation_id,
            status=admission.status,
            execution_status=admission.execution_status,
        )

    return MessageGateway(MessageGatewayPorts(admit_sync=admit_sync))
