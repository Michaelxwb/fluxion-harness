from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .tasks import ContractModel


class ChannelEnvelope(ContractModel):
    channel: Literal["WECOM"]
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    external_conversation_id: str | None = None
    message_id: str = Field(min_length=1)
    text: str = ""


class ChannelResolveRequest(ContractModel):
    channel: Literal["WECOM"]
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)


class ChannelResolveResponse(ContractModel):
    bound: bool
    agent_id: UUID | None = None
    platform_user_id: UUID | None = None
    authorized: bool = False


class ChannelBindRequest(ContractModel):
    channel: Literal["WECOM"]
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    bind_code: str = Field(min_length=1)


class ChannelBindResponse(ContractModel):
    platform_user_id: UUID
    bound: bool = True


class BotSnapshotItem(ContractModel):
    bot_account_id: UUID
    bot_id: str
    secret: str | None = None
    agent_id: UUID
    enabled: bool = True


class BotSnapshotResponse(ContractModel):
    revision: str
    items: list[BotSnapshotItem] = Field(default_factory=list)
