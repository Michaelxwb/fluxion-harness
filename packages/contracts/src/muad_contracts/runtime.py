from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ChannelContext(BaseModel):
    type: str
    bot_id: str
    external_conversation_id: str | None = None


class MessageInput(BaseModel):
    id: str
    type: str = "text"
    text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class RunRequest(BaseModel):
    agent_id: UUID
    platform_user_id: UUID
    conversation_id: UUID | None = None
    channel: ChannelContext
    message: MessageInput
