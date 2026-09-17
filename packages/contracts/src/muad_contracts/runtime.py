from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .tasks import ContractModel


class ChannelContext(ContractModel):
    type: Literal["WECOM"]
    bot_id: str = Field(min_length=1)
    external_conversation_id: str | None = None


class MessageInput(ContractModel):
    id: str = Field(min_length=1)
    type: Literal["text"] = "text"
    text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class RunRequest(ContractModel):
    agent_id: UUID
    platform_user_id: UUID
    conversation_id: UUID | None = None
    channel: ChannelContext
    message: MessageInput
