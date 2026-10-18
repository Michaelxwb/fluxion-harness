"""Webchat Owner 端点（design §3.4 API-09/11/14/15；API-12 内部面；TASK-050/058/064）。

- 只对受信服务身份开放（`X-Internal-Service`，与 `/internal/admin/runs` 同门控）；租户从部署
  中间件导出，主体（`user_id`）与入口/Agent 由受信调用方显式携带 —— 浏览器自报字段没有位置。
- **入口显式**：列表走路由；详情/消息也要求 `entry_id`+`agent_id`（TASK-052 的公共路径不带
  入口，先经 `.../scope` 按 (tenant,user) 解析归属再调用），核属复用 TASK-049
  （tenant/user/agent/入口 同时成立，404 不泄露存在性）。
- **暂存上传登记**（`POST /internal/webchat/uploads`，TASK-058）：Gateway 把字节写进共享存储后
  只在这里登记权威元信息；入口/Agent 核属（`assert_entry_agent_binding`）与元信息边界由登记原语
  统一裁决，响应只回 `upload_id` 与权威元信息（无存储键、无预造制品引用）。
- **下载/预览解析**（`GET /internal/webchat/artifacts/{id}`，TASK-055）：`artifact_id` → 本主体
  名下会话的归属 + 渠道中立引用（含存储键，只在受信面传递）；跨租户/他主体/已失效一律 404。
- **活动收件箱**（`GET /internal/webchat/notifications`、`POST .../{id}/read`、
  `POST /internal/webchat/task-results`，TASK-064）：按用户 feed seq 增量读取、显式已读（独立于
  投递状态）与受信 Task 结果入箱（核真实来源，持久入箱后才回 `persisted`）。
- 响应只含 `WebchatHistoryService`/`ChannelNotificationQueryService` 的安全公开投影；不暴露
  快照、存储键、投递/租约内部字段或内部事件。
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
from muad_contracts.webchat import WebchatUpload, WebchatUploadRegistration
from muad_contracts.webchat_events import WebchatTaskResultRegistration

from ..application.channel_notification_queries import (
    ChannelNotificationQueryService,
    NotificationScope,
)
from ..application.channel_uploads import (
    assert_entry_agent_binding,
    register_staged_upload,
    upload_limits_from_settings,
)
from ..application.ports import PlatformSettingsClient
from ..application.webchat_artifacts import resolve_webchat_artifact
from ..application.webchat_history import HistoryScope, WebchatHistoryService
from ..infrastructure.models.runtime import ChannelUpload
from .deps import PlatformSettingsClientDep, SessionDep, TenantDep

router = APIRouter(
    prefix="/internal/webchat",
    tags=["webchat"],
    dependencies=[Depends(require_internal_service)],
)

PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)]
UserId = Annotated[uuid.UUID, Query()]
AgentId = Annotated[uuid.UUID, Query()]
EntryId = Annotated[uuid.UUID, Query()]
ThroughSeq = Annotated[int | None, Query(ge=0)]
AfterSeq = Annotated[int, Query(ge=0)]


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


def _upload_view(row: ChannelUpload) -> WebchatUpload:
    """登记行 → 权威元信息投影（API-10 的 `upload_id` 与元信息；存储键不进响应）。"""
    return WebchatUpload.model_validate(
        {
            "upload_id": row.id,
            "kind": row.kind,
            "media_type": row.media_type,
            "size": row.size,
            "filename": row.filename,
            "checksum": row.checksum,
            "expires_at": row.expires_at,
        }
    )


@router.get("/artifacts/{artifact_id}")
async def resolve_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    user_id: UserId,
) -> ApiResponse[Any]:
    """API-10 的 Owner 侧解析（TASK-055）：本主体名下会话的附件 → 会话归属 + 渠道中立引用。

    只接受受信主体 `user_id`（浏览器自报字段没有位置）：入口/Agent 从会话行导出，Gateway 据此
    复核**当前**入口/Agent 授权。跨租户/他主体/已失效/内部留档一律 `COMMON_NOT_FOUND`（不泄露
    存在性）；存储键只在本受信面返回，由 Gateway 取字节、绝不进公开响应。
    """
    download = await resolve_webchat_artifact(
        session, tenant_id=tenant_id, user_id=user_id, artifact_id=artifact_id
    )
    return ok(
        request.app.state.message_catalog,
        {
            "scope": download.scope.model_dump(mode="json"),
            "reference": download.reference.model_dump(mode="json"),
        },
    )


@router.post("/uploads")
async def register_staged_upload_endpoint(
    body: WebchatUploadRegistration,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    settings_client: PlatformSettingsClientDep,
) -> ApiResponse[Any]:
    """API-12：Gateway 写共享字节后经受信端口登记暂存上传（返回 `upload_id` 与权威元信息）。

    身份三件套由受信调用方显式携带（已验证 CHAT 主体 + 入口解析）；入口/Agent 核属与元信息
    边界由登记原语统一裁决（稳定错误码）；只落 `channel_upload` 行，不预造 artifact。
    """
    snapshot = await settings_client.fetch_snapshot(tenant_id=tenant_id)
    await assert_entry_agent_binding(
        session, tenant_id=tenant_id, entry_id=body.entry_id, agent_id=body.agent_id
    )
    row = await register_staged_upload(
        session,
        tenant_id=tenant_id,
        user_id=body.user_id,
        entry_id=body.entry_id,
        storage_key=body.storage_key,
        kind=body.kind,
        media_type=body.media_type,
        checksum=body.checksum,
        filename=body.filename,
        size=body.size,
        limits=upload_limits_from_settings(snapshot.settings),
    )
    await session.commit()
    return ok(request.app.state.message_catalog, _upload_view(row))


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


@router.get("/conversations/{conversation_id}/scope")
async def get_conversation_scope(
    conversation_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    user_id: UserId,
) -> ApiResponse[dict[str, Any]]:
    """API-09/15 的入口解析（TASK-052）：公共路径不带入口 ⇒ 网关先解析归属再走入口显式读取。

    只要求受信主体 `user_id`；入口/Agent 从会话行导出，调用方不能自报（也不泄露他主体存在性）。
    """
    scope = await WebchatHistoryService(session).resolve_conversation_scope(
        tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id
    )
    return ok(request.app.state.message_catalog, scope.model_dump(mode="json"))


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


@router.get("/notifications")
async def list_notifications(
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    settings_client: PlatformSettingsClientDep,
    user_id: UserId,
    after_seq: AfterSeq = 0,
    page: PageNumber = 1,
    page_size: PageSize = None,
) -> ApiResponse[dict[str, Any]]:
    """API-11：按用户 feed seq 增量分页（`next_seq` 为下一次读取水位）。"""
    size = await _effective_page_size(settings_client, tenant_id, page_size)
    page_view = await ChannelNotificationQueryService(session).list_notifications(
        NotificationScope(tenant_id=tenant_id, user_id=user_id),
        after_seq=after_seq,
        page=page,
        page_size=size,
    )
    return ok(request.app.state.message_catalog, page_view.model_dump(mode="json"))


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    user_id: UserId,
) -> ApiResponse[dict[str, Any]]:
    """API-11：显式已读（幂等；与投递状态独立；只标记明确读过的行）。"""
    receipt = await ChannelNotificationQueryService(session).mark_read(
        NotificationScope(tenant_id=tenant_id, user_id=user_id), notification_id
    )
    await session.commit()
    return ok(request.app.state.message_catalog, receipt.model_dump(mode="json"))


@router.post("/task-results")
async def register_task_result(
    body: WebchatTaskResultRegistration,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
) -> ApiResponse[dict[str, Any]]:
    """受信 Task 结果入箱（TASK-064）：核真实来源；提交后才回 `persisted=true`。"""
    receipt = await ChannelNotificationQueryService(session).register_task_result(
        tenant_id=tenant_id, registration=body
    )
    await session.commit()
    return ok(request.app.state.message_catalog, receipt.model_dump(mode="json"))
