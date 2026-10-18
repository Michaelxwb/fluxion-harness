"""Webchat 公开资源路由（design §3.4 API-05/06/08/09/10/14/15；TASK-052/TASK-054）。

- **主体门控**：读写面统一经 `ChatIdentityDep`（TASK-044）收口——CHAT Cookie + `X-Chat-Context`
  只经 Console 核验，跨部署租户/上下文不符/撤销在这里就被拒；本层不做第二套认证。
- **完整转 Owner**：入口/会话/消息/取消/恢复全部经 `WebchatResourceService` 走 Console/Runtime
  的受信接口；网关不持库（架构守卫机检零数据库依赖）。
- **契约形状**：请求体/查询参数用 `muad_contracts.webchat` 的公开契约解析（多余字段显式拒绝），
  响应 data 就是契约的 JSON 投影；202/201 是**受理事实**的真实状态码。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse
from muad_api import ApiResponse, AppError, ok
from muad_api.error_codes import ErrorCode
from muad_contracts import MAX_PAGE_SIZE
from muad_contracts.webchat import (
    WebchatHistoryQuery,
    WebchatMessageRequest,
    WebchatResumeRequest,
)
from pydantic import ValidationError

from ..application.bot_snapshot import BotSnapshotCache
from ..application.runtime_client import RuntimeClient
from ..application.webchat_artifacts import (
    NOSNIFF_HEADER,
    ArtifactDownload,
    WebchatArtifactService,
    content_disposition_header,
)
from ..application.webchat_service import WebchatResourceService
from ..application.webchat_uploads import WebchatUploadService
from .deps import ChatIdentityDep

#: 聊天资源是私有数据（design §3.4）：显式 no-store，不依赖任何默认缓存头。
NO_STORE_CACHE_CONTROL = "no-store"


async def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE_CACHE_CONTROL


router = APIRouter(
    prefix="/api/v1/webchat", tags=["webchat"], dependencies=[Depends(no_store)]
)

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)]
ThroughSeq = Annotated[int | None, Query(ge=0)]


def get_webchat_service(request: Request) -> WebchatResourceService:
    """资源服务：Owner 客户端 + 入口快照 + 部署租户（装配根发布的状态值，网关不持库）。"""
    runtime: RuntimeClient = request.app.state.runtime_client
    entries: BotSnapshotCache = request.app.state.bot_snapshot
    tenant_id: str = request.app.state.tenant_id
    return WebchatResourceService(runtime=runtime, entries=entries, tenant_id=tenant_id)


WebchatServiceDep = Annotated[WebchatResourceService, Depends(get_webchat_service)]


def get_webchat_upload_service(request: Request) -> WebchatUploadService:
    """上传服务：Owner 客户端 + 入口快照 + 设置源 + 共享存储 + 启动硬预算（装配根发布的状态值）。"""
    runtime: RuntimeClient = request.app.state.runtime_client
    entries: BotSnapshotCache = request.app.state.bot_snapshot
    return WebchatUploadService(
        runtime=runtime,
        entries=entries,
        settings=request.app.state.settings_client,
        store=request.app.state.artifact_store,
        tenant_id=request.app.state.tenant_id,
        hard_max_bytes=request.app.state.chat_upload_hard_max_bytes,
    )


WebchatUploadServiceDep = Annotated[WebchatUploadService, Depends(get_webchat_upload_service)]


def get_webchat_artifact_service(request: Request) -> WebchatArtifactService:
    """下载/预览服务：Owner 客户端 + 入口快照 + 共享存储 + 部署租户（网关不持库）。"""
    runtime: RuntimeClient = request.app.state.runtime_client
    entries: BotSnapshotCache = request.app.state.bot_snapshot
    return WebchatArtifactService(
        runtime=runtime,
        entries=entries,
        store=request.app.state.artifact_store,
        tenant_id=request.app.state.tenant_id,
    )


WebchatArtifactServiceDep = Annotated[
    WebchatArtifactService, Depends(get_webchat_artifact_service)
]


def _download_response(download: ArtifactDownload) -> FileResponse:
    """取件响应：`no-store`/`nosniff` + 服务端裁决的 `Content-Disposition`。

    存储路径与 `storage_key` 一步都不出现在响应里；HTML/SVG 等可执行内容按附件下载
    （禁止同源脚本执行），只有白名单栅格图片 `inline` 预览。
    """
    return FileResponse(
        path=download.path,
        media_type=download.media_type,
        stat_result=download.stat,
        headers={
            "Cache-Control": NO_STORE_CACHE_CONTROL,
            "X-Content-Type-Options": NOSNIFF_HEADER,
            "Content-Disposition": content_disposition_header(
                media_type=download.media_type, filename=download.filename
            ),
        },
    )


@router.get("/entries")
async def list_entries(
    request: Request, chat: ChatIdentityDep, service: WebchatServiceDep
) -> ApiResponse[Any]:
    """API-05：仅列已启用且当前用户获 Agent 授权的入口。"""
    page = service.list_entries(chat)
    return ok(request.app.state.message_catalog, page.model_dump(mode="json"))


@router.post("/entries/{entry_id}/messages", status_code=202)
async def submit_message(
    entry_id: uuid.UUID,
    payload: WebchatMessageRequest,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatServiceDep,
    idempotency_key: IdempotencyKey,
) -> ApiResponse[Any]:
    """API-06：显式会话提交；受理持久化后才返回 202，长流不占本处理器。"""
    accepted = await service.submit_message(
        chat, entry_id, payload, idempotency_key=idempotency_key
    )
    return ok(request.app.state.message_catalog, accepted.model_dump(mode="json"))


@router.post("/entries/{entry_id}/conversations", status_code=201)
async def create_conversation(
    entry_id: uuid.UUID,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatServiceDep,
    idempotency_key: IdempotencyKey,
) -> ApiResponse[Any]:
    """API-08：新建会话（201）；身份与标题由服务端持有，幂等键由调用方沿用。"""
    created = await service.create_conversation(chat, entry_id, idempotency_key=idempotency_key)
    return ok(request.app.state.message_catalog, created.model_dump(mode="json"))


@router.post("/entries/{entry_id}/attachments", status_code=201)
async def upload_attachment(
    entry_id: uuid.UUID,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatUploadServiceDep,
) -> ApiResponse[Any]:
    """API-10：流式接收上传字节写共享存储后转 Runtime 登记（201 = 真实创建事实）。

    限额/MIME/数量门控来自平台设置与既有附件词表（不硬编码）；超限在写完之前中止并清理；
    响应只含 `upload_id` 与权威元信息，不伪造提前存在的 artifact_id。
    """
    upload = await service.upload(
        chat, entry_id, stream=request.stream(), content_type=request.headers.get("content-type")
    )
    return ok(request.app.state.message_catalog, upload.model_dump(mode="json"))


@router.get("/entries/{entry_id}/conversations")
async def list_conversations(
    entry_id: uuid.UUID,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatServiceDep,
    page: PageNumber = 1,
    page_size: PageSize = None,
) -> ApiResponse[Any]:
    """API-14：入口内会话索引（按最近活动倒序由 Owner 保证）。"""
    view = await service.list_conversations(chat, entry_id, page=page, page_size=page_size)
    return ok(request.app.state.message_catalog, view.model_dump(mode="json"))


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatServiceDep,
) -> ApiResponse[Any]:
    """API-15：核属后读取会话与活跃 Run（公共路径不带入口，由 Owner 解析归属）。"""
    detail = await service.get_conversation(chat, conversation_id)
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatServiceDep,
    page: PageNumber = 1,
    page_size: PageSize = None,
    through_seq: ThroughSeq = None,
) -> ApiResponse[Any]:
    """API-09：固定水位分页（首屏冻结，后页沿用；page>1 必须带 through_seq）。"""
    try:
        WebchatHistoryQuery(page=page, page_size=page_size, through_seq=through_seq)
    except ValidationError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc
    view = await service.list_messages(
        chat,
        conversation_id,
        page=page,
        page_size=page_size,
        through_seq=through_seq,
    )
    return ok(request.app.state.message_catalog, view.model_dump(mode="json"))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatServiceDep,
) -> ApiResponse[Any]:
    """API-08：精确取消原 Run（可重放；终态重放不再产生副作用）。"""
    result = await service.cancel_run(chat, run_id)
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: uuid.UUID,
    payload: WebchatResumeRequest,
    request: Request,
    chat: ChatIdentityDep,
    service: WebchatServiceDep,
) -> ApiResponse[Any]:
    """API-08：显式绑定 WAITING_INPUT 的原 Run/submission/source 恢复。"""
    receipt = await service.resume_run(chat, run_id, payload)
    return ok(request.app.state.message_catalog, receipt.model_dump(mode="json"))


@router.get("/artifacts/{artifact_id}/content")
async def download_artifact(
    artifact_id: uuid.UUID,
    chat: ChatIdentityDep,
    service: WebchatArtifactServiceDep,
) -> FileResponse:
    """API-10：附件下载/图片预览（逐请求复核会话归属与当前入口/Agent 授权）。

    客户端只给 `artifact_id`：存储键由 Owner 解析、由网关从共享存储读取（不凭客户端取件键取件）；
    未授权/他主体/已失效一律 404、授权撤销 403，都不泄露存在性。
    """
    download = await service.open_download(chat, artifact_id)
    return _download_response(download)
