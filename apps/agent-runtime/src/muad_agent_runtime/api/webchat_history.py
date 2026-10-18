"""Webchat 历史 Owner 端点（design §3.4 API-09/14/15；API-12 内部面；TASK-050）。

- 只对受信服务身份开放（`X-Internal-Service`，与 `/internal/admin/runs` 同门控）；租户从部署
  中间件导出，主体（`user_id`）与入口/Agent 由受信调用方显式携带 —— 浏览器自报字段没有位置。
- **入口显式**：列表走路由；详情/消息也要求 `entry_id`+`agent_id`（TASK-052 从 `/chat/:entryId`
  路由参数与入口快照解析），核属复用 TASK-049（tenant/user/agent/入口 同时成立，404 不泄露存在性）。
- 响应只含 `WebchatHistoryService` 的安全公开投影；不暴露快照、存储键或内部事件。
- 页大小默认值来自 Console 平台设置（`webchat.history_page_size`），不在端点里硬编码。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_contracts import MAX_PAGE_SIZE
from muad_contracts.platform_settings import parse_platform_settings

from ..application.ports import PlatformSettingsClient
from ..application.webchat_history import HistoryScope, WebchatHistoryService
from .deps import PlatformSettingsClientDep, SessionDep, TenantDep

router = APIRouter(
    prefix="/internal/webchat",
    tags=["webchat-history"],
    dependencies=[Depends(require_internal_service)],
)

PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)]
UserId = Annotated[uuid.UUID, Query()]
AgentId = Annotated[uuid.UUID, Query()]
EntryId = Annotated[uuid.UUID, Query()]
ThroughSeq = Annotated[int | None, Query(ge=0)]


async def _effective_page_size(
    settings_client: PlatformSettingsClient, tenant_id: str, page_size: int | None
) -> int:
    """页大小默认值来自平台设置（Console 权威）；设置源损坏时显式失败，不静默回落。"""
    if page_size is not None:
        return page_size
    snapshot = await settings_client.fetch_snapshot(tenant_id=tenant_id)
    return parse_platform_settings(snapshot.settings).webchat.history_page_size


def _scope(
    tenant_id: str, *, user_id: uuid.UUID, agent_id: uuid.UUID, entry_id: uuid.UUID
) -> HistoryScope:
    return HistoryScope(
        tenant_id=tenant_id, user_id=user_id, agent_id=agent_id, entry_id=entry_id
    )


@router.get("/entries/{entry_id}/conversations")
async def list_entry_conversations(
    entry_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    settings_client: PlatformSettingsClientDep,
    user_id: UserId,
    agent_id: AgentId,
    page: PageNumber = 1,
    page_size: PageSize = None,
) -> ApiResponse[dict[str, Any]]:
    """API-14：入口会话列表（仅本主体/入口/Agent，按最近活动倒序）。"""
    size = await _effective_page_size(settings_client, tenant_id, page_size)
    page_view = await WebchatHistoryService(session).list_conversations(
        _scope(tenant_id, user_id=user_id, agent_id=agent_id, entry_id=entry_id),
        page=page,
        page_size=size,
    )
    return ok(request.app.state.message_catalog, page_view.model_dump(mode="json"))


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    user_id: UserId,
    agent_id: AgentId,
    entry_id: EntryId,
) -> ApiResponse[dict[str, Any]]:
    """API-15：核属后的会话详情（活跃 Run 供打开历史后的恢复）。"""
    detail = await WebchatHistoryService(session).get_conversation(
        _scope(tenant_id, user_id=user_id, agent_id=agent_id, entry_id=entry_id), conversation_id
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    settings_client: PlatformSettingsClientDep,
    user_id: UserId,
    agent_id: AgentId,
    entry_id: EntryId,
    page: PageNumber = 1,
    page_size: PageSize = None,
    through_seq: ThroughSeq = None,
) -> ApiResponse[dict[str, Any]]:
    """API-09：可见消息固定水位分页（首屏冻结 `through_seq`，后页沿用）。"""
    size = await _effective_page_size(settings_client, tenant_id, page_size)
    page_view = await WebchatHistoryService(session).list_messages(
        _scope(tenant_id, user_id=user_id, agent_id=agent_id, entry_id=entry_id),
        conversation_id,
        page=page,
        page_size=size,
        through_seq=through_seq,
    )
    return ok(request.app.state.message_catalog, page_view.model_dump(mode="json"))
