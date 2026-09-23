import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, ok
from muad_contracts import DEFAULT_PAGE_SIZE, ChannelBindRequest, ChannelResolveRequest
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.channel_service import ChannelService
from ..application.channel_skills_service import ChannelSkillsService
from ..infrastructure.db import get_session
from .deps import get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
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
    bound = await ChannelService(session).bind(tenant_id, payload, idempotency_key)
    return ok(request.app.state.message_catalog, bound.model_dump(mode="json"))


@router.get("/bots")
async def bots(
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    snapshot = await ChannelService(session).bots(tenant_id)
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
