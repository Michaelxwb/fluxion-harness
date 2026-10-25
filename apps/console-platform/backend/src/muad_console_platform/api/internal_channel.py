import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, InternalServiceDep, ok
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    ArtifactDeliveryAuditRequest,
    ChannelBindRequest,
    ChannelResolveRequest,
    InboundAuditRequest,
)
from muad_contracts.webchat import ChatPrincipalRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.artifact_delivery_audit_service import ArtifactDeliveryAuditService
from ..application.channel_identity_service import ChannelIdentityService
from ..application.channel_service import ChannelService
from ..application.channel_skills_service import ChannelSkillsService
from ..application.inbound_audit_service import InboundAuditService
from ..infrastructure.db import get_session
from ..metrics import BIND_METRIC, count_outcome
from .deps import HeaderTenantId

# 内部端点无用户会话，租户由调用方（IM Gateway，已有 InternalServiceDep 门控）显式声明。
TenantId = HeaderTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/internal/channel", tags=["internal-channel"])


@router.post("/resolve")
async def resolve(
    payload: ChannelResolveRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    resolved = await ChannelService(session).resolve(tenant_id, payload)
    return ok(request.app.state.message_catalog, resolved.model_dump(mode="json"))


@router.post("/bind")
async def bind(
    payload: ChannelBindRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    with count_outcome(BIND_METRIC):
        bound = await ChannelService(session).bind(tenant_id, payload, idempotency_key)
    return ok(request.app.state.message_catalog, bound.model_dump(mode="json"))


@router.get("/bots")
async def bots(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    internal_service: InternalServiceDep,
    page: Annotated[int, Query()] = 1,
    page_size: Annotated[int, Query()] = DEFAULT_PAGE_SIZE,
    channels: Annotated[str | None, Query()] = None,
    include_secret: Annotated[bool, Query()] = True,
) -> ApiResponse[Any]:
    """入口快照：`channels`（逗号分隔）限定调用方拥有的通道，避免泄露别人的 bot secret。"""
    wanted = (
        frozenset(item.strip().upper() for item in channels.split(",") if item.strip())
        if channels
        else None
    )
    snapshot = await ChannelService(session).bots(
        tenant_id,
        page=page,
        page_size=page_size,
        channels=wanted,
        include_secret=include_secret,
    )
    return ok(request.app.state.message_catalog, snapshot.model_dump(mode="json"))


@router.get("/skills")
async def skills(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    agent_id: Annotated[uuid.UUID, Query()],
    platform_user_id: Annotated[uuid.UUID, Query()],
    page: Annotated[int, Query()] = 1,
    page_size: Annotated[int, Query()] = DEFAULT_PAGE_SIZE,
) -> ApiResponse[Any]:
    skills_response = await ChannelSkillsService(session).list_skills(
        tenant_id,
        agent_id,
        platform_user_id,
        page=page,
        page_size=page_size,
    )
    return ok(request.app.state.message_catalog, skills_response.model_dump(mode="json"))


@router.post("/audit")
async def audit(
    payload: InboundAuditRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    internal_service: InternalServiceDep,
) -> ApiResponse[Any]:
    """入站事件审计写入（设计 API-10）——**网关唯一的审计出口**。

    网关不持库，只能把"谁发的什么被拒了、为什么"交到这里；入参契约全字段枚举化，
    取件凭据在类型上无处可放。重复投递返回既有行（幂等键见服务层）。
    """
    audit_id = await InboundAuditService(session).record(tenant_id, payload)
    return ok(request.app.state.message_catalog, {"id": str(audit_id)})


@router.post("/artifact-delivery")
async def artifact_delivery(
    payload: ArtifactDeliveryAuditRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    internal_service: InternalServiceDep,
) -> ApiResponse[Any]:
    """交付审计写入（设计 API-03）——**网关唯一的交付留痕出口**。

    网关不持库，只能把"哪个产物交付给哪个路由、结果如何"交到这里；入参契约全字段具名化，
    交付凭据与取件令牌在类型上无处可放。同键重写**更新既有行**而不是新增（幂等键见服务层）；
    已 `DELIVERED` 的行是终态，后到的写落空。
    """
    audit_id = await ArtifactDeliveryAuditService(session).record(tenant_id, payload)
    return ok(request.app.state.message_catalog, {"id": str(audit_id)})


@router.post("/chat-principal")
async def chat_principal(
    payload: ChatPrincipalRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    internal_service: InternalServiceDep,
    chat_context: Annotated[uuid.UUID, Header(alias="X-Chat-Context")],
) -> ApiResponse[Any]:
    """API-12 受信聊天身份核验与授权解析（设计 §3.4）。

    服务身份与部署租户先过门控；CHAT 会话原文只作输入凭据，主体与授权集合只从 PG
    会话/来源 Token/用户/授权行导出（浏览器自报 user_id/tenant 在契约层无处可放）。
    `X-Chat-Context` 必须等于会话自身的 `auth_context_id`，否则 409（防 Cookie 切用户误用）。
    CHAT 身份不授予 Console 权限，凭据原文不进响应、日志与审计。
    """
    resolved = await ChannelIdentityService(session).resolve_chat_principal(
        tenant_id, payload, chat_context=chat_context
    )
    return ok(request.app.state.message_catalog, resolved.model_dump(mode="json"))
