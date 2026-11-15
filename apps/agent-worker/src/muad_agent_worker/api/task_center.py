"""任务中心（TaskCenter）的 Worker 侧读面：待处理与徽标。

Surface（WebChat / MSSWWEB）由各自的 ChatSurface 挂载对外路由，这里只提供同一份数据的
内部来源：**同一 Agent 范围**贯穿列表、详情、待处理、徽标与回答，不按 entry/channel 过滤
（用户的定时任务跨入口共享，见 design §3.2.7）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.schedule_read import ScheduleReadService
from ..infrastructure.db import get_session
from .deps import ActorUserId, get_tenant_id

router = APIRouter(
    prefix="/internal/task-center",
    tags=["task-center"],
    dependencies=[Depends(require_internal_service)],
)

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/pending")
async def list_pending(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: ActorUserId,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    kind: Annotated[Literal["HUMAN", "NEEDS_ATTENTION"] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    """API-16 的数据源：HUMAN 与 NEEDS_ATTENTION 混排后统一分页。"""
    result = await ScheduleReadService(session).pending_page(
        tenant_id=tenant_id,
        owner_user_id=caller_actor,
        agent_ids=[agent_id] if agent_id is not None else None,
        kind=kind,
        page=page,
        page_size=page_size,
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.get("/badges")
async def get_badges(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: ActorUserId,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ApiResponse[Any]:
    """API-17 的数据源：与列表/待处理同一 Agent 范围，否则徽标会与页面数字打架。"""
    badges = await ScheduleReadService(session).badges(
        tenant_id=tenant_id,
        owner_user_id=caller_actor,
        agent_ids=[agent_id] if agent_id is not None else None,
    )
    return ok(request.app.state.message_catalog, badges.model_dump(mode="json"))
