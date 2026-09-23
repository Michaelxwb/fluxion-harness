from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .tasks import ContractModel

# 与 api-kit `muad_api.response` 的分页边界保持一致（contracts 为独立包，不反向依赖 api-kit）
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class PageMeta(ContractModel):
    """统一列表分页字段（required API Rule：page>=1、1<=page_size<=100）。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    total: int = Field(default=0, ge=0)


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
    external_conversation_id: str | None = None


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


class ChannelSkillItem(ContractModel):
    """API-04 允许字段；不含 SKILL.md 全文或未授权资源。"""

    skill_id: UUID
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    platform_label: str = Field(min_length=1)
    description: str = ""


class ChannelSkillsResponse(PageMeta):
    items: list[ChannelSkillItem] = Field(default_factory=list)


class BotSnapshotItem(ContractModel):
    bot_account_id: UUID
    bot_id: str
    # 仅内存/受保护内部快照使用：不从 repr/异常详情回显
    secret: str | None = Field(default=None, repr=False)
    agent_id: UUID
    enabled: bool = True


class BotSnapshotResponse(PageMeta):
    revision: str
    items: list[BotSnapshotItem] = Field(default_factory=list)
