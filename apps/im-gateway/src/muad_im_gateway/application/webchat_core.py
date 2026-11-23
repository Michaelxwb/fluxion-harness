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
from muad_logging import get_logger

from .runtime_client import RunAdmission

logger = get_logger(__name__)

#: WEBCHAT 通道名常量；其余同构通道（如 MSSWWEB）由 `WebchatSurface.channel` 携带，
#: 本模块不再新增通道字面量——把通道名参数化，才能同一套 SYNC 逻辑按通道多挂载。
WEBCHAT_CHANNEL: ChannelName = "WEBCHAT"


class SyncRunOwner(Protocol):
    """SYNC 受理只要 Owner 的一个动词（Runtime 是唯一实现；结构替身同形）。"""

    async def submit_run(
        self, request: RunRequest, *, tenant_id: str, idempotency_key: str
    ) -> RunAdmission: ...


@dataclass(frozen=True, slots=True)
class WebchatChannelIdentity:
    """统一核心的通道身份：与注册表里对应 adapter 的 `name` 同值。"""

    channel: ChannelName = WEBCHAT_CHANNEL


WEBCHAT_CHANNEL_IDENTITY = WebchatChannelIdentity()


@dataclass(frozen=True, slots=True)
class ChatSurface:
    """一个「本地流式对话通道」的装配上下文（设计 §ChatSurface 抽象）。

    WEBCHAT 与 MSSWWEB 共享同一套 router / service / core，差异只有 `channel` 名与
    认证方式（`auth_kind`）。把通道名放进这个对象按 surface 注入，避免在 application
    层写死任一通道字面量（架构用例 test_b09 会拒绝）。
    """

    channel: ChannelName
    #: 认证方式：`USER_TOKEN`（WebChat：Console 签发 CHAT token）或
    #: `EXTERNAL_ASSERTION`（MSSWWEB：上游 platform 断言）。认证分支在 `api/deps.py` 落地。
    auth_kind: str = "USER_TOKEN"
    #: 外部断言面所属的 `platform_key`（仅 `EXTERNAL_ASSERTION` 用到）；来自 Secret 配置，
    #: 视为非敏感标识。Gateway 转发到 Console 的 `external-principal` 时原样带上。
    platform_key: str | None = None
    #: 外部断言面读取 JWT 的请求头名（仅 `EXTERNAL_ASSERTION` 用到）；来自 Secret 配置，
    #: 默认 `soc-token`。不同外部平台可各配各的头名，故按 surface 注入而非写死在 deps。
    assertion_header: str = "soc-token"

    @property
    def identity(self) -> WebchatChannelIdentity:
        return WebchatChannelIdentity(channel=self.channel)


def _run_request(event: InboundEvent, principal: SyncPrincipal, *, channel: ChannelName) -> RunRequest:
    """渠道中立事件 + 已验证主体 → Runtime 提交契约（显式会话、暂存上传原样保留）。"""
    if event.external_conversation_id is None:
        # 本地流式提交**永远**带显式会话（API-06 形状）：缺失是调用方错误，不猜最新指针。
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return RunRequest(
        agent_id=principal.agent_id,
        platform_user_id=principal.platform_user_id,
        conversation_id=UUID(event.external_conversation_id),
        channel=ChannelContext(
            type=channel,
            entry_id=principal.entry_id,
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


def build_webchat_message_gateway(
    owner: SyncRunOwner, *, channel: ChannelName = WEBCHAT_CHANNEL
) -> MessageGateway:
    """装配本地流式 SYNC 端口：核心做通道校验，端口只做事件→提交契约的映射。"""

    async def admit_sync(event: InboundEvent, principal: SyncPrincipal) -> SyncAdmission:
        request = _run_request(event, principal, channel=channel)
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
