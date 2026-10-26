from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .channel import AttachmentRef
from .enums import ChannelName
from .tasks import ContractModel


class ChannelContext(ContractModel):
    type: ChannelName = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    external_user_id: str | None = None
    external_conversation_id: str | None = None


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
