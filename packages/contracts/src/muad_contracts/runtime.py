from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .channel import AttachmentRef
from .enums import ChannelName
from .tasks import ContractModel


class ChannelContext(ContractModel):
    """入站渠道身份：**入口逻辑 UUID 与平台 bot id 是两件事**。

    - `entry_id`：入口账号的逻辑 UUID（`control.bot_account.id`）——会话归属、入口指针、
      权限边界与终态通知的身份。由入口解析（Console `resolve`）给出，**不从字符串形状推断**；
    - `bot_id`：平台侧 bot 标识——Connector 连接、租约与出站路由按它定位，与入口 UUID 无关。
    """

    type: ChannelName = Field(min_length=1)
    entry_id: UUID
    bot_id: str = Field(min_length=1)
    external_user_id: str | None = None
    external_conversation_id: str | None = None

    @property
    def external_thread_id(self) -> str:
        """外线线程：优先外部会话（如 chatid），1:1 回落外部用户（与 0025 回填同一口径）。"""
        return self.external_conversation_id or self.external_user_id or ""


class CancelSource(ContractModel):
    """精确取消的来源身份（RISK-F03：不用「用户+Agent 最新 Run」定位）。

    `entry_id` 必填（入口身份永远存在）；显式会话用 `conversation_id` 精确到会话，
    隐式入口按 (入口, 外线线程) 定位当前指针。两者都给时以会话为准，入口归属仍要一致。
    """

    entry_id: UUID
    conversation_id: UUID | None = None
    external_thread_id: str = ""


class MessageInput(ContractModel):
    id: str = Field(min_length=1)
    # 缺省仍为 "text"：既有只发文本的调用方无需改动（向后兼容）
    type: Literal["text", "attachment"] = "text"
    text: str = ""
    attachments: list[AttachmentRef] = Field(default_factory=list)
    #: 网页入口的暂存上传引用（API-06 `upload_ids[]`）：只带不透明 upload_id，
    #: 权威元信息与存储键在 Runtime 侧按登记核属（design §3.3：不通过客户端 storage_key 取件）。
    upload_ids: list[UUID] = Field(default_factory=list)


class RunRequest(ContractModel):
    agent_id: UUID
    platform_user_id: UUID
    conversation_id: UUID | None = None
    channel: ChannelContext
    message: MessageInput
