"""任务中心（TaskCenter）的 Worker 侧读面：待处理与徽标。

Surface（WebChat / MSSWWEB）由各自的 ChatSurface 挂载对外路由，这里只提供同一份数据的
内部来源：**同一 Agent 范围**贯穿列表、详情、待处理、徽标与回答，不按 entry/channel 过滤
（用户的定时任务跨入口共享，见 design §3.2.7）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, AppError, ok
from muad_api.error_codes import ErrorCode
from muad_api.security import require_internal_service
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.schedule_read import DeliveryRouteScope, ScheduleReadService
from ..infrastructure.db import get_session
from ..metrics import TASK_QUERY_ONLY_METRIC, increment
from .deps import ActorUserId, get_tenant_id

router = APIRouter(
    prefix="/internal/task-center",
    tags=["task-center"],
    dependencies=[Depends(require_internal_service)],
)

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _route_scope(
    request: Request,
    channel: str | None,
    bot_id: str | None,
    external_user_id: str | None,
    external_conversation_id: str | None,
) -> DeliveryRouteScope | None:
    """来源参数要么都缺省（任务中心视角），要么 channel+bot_id 齐备（会话视角）。"""
    if channel is None and bot_id is None and external_user_id is None and external_conversation_id is None:
        return None
    if not channel or not bot_id:
        raise AppError(
            ErrorCode.COMMON_VALIDATION_ERROR,
            data={"fields": ["channel", "bot_id"]},
        )
    return DeliveryRouteScope(
        channel=channel,
        bot_id=bot_id,
        external_user_id=external_user_id,
        external_conversation_id=external_conversation_id,
    )


@router.get("/pending")
async def list_pending(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    caller_actor: ActorUserId,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    kind: Annotated[Literal["HUMAN", "NEEDS_ATTENTION"] | None, Query()] = None,
    channel: Annotated[str | None, Query(max_length=32)] = None,
    bot_id: Annotated[str | None, Query(max_length=256)] = None,
    external_user_id: Annotated[str | None, Query(max_length=256)] = None,
    external_conversation_id: Annotated[str | None, Query(max_length=256)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Any]:
    """API-16 的数据源：HUMAN 与 NEEDS_ATTENTION 混排后统一分页。

    四个来源参数成组出现（channel + bot_id 是必填的一半）：给了就只返回**推到这个会话**的
    提问——聊天入口回答时按 `user+agent+会话` 匹配，绝不按文本或新旧猜。
    """
    result = await ScheduleReadService(session).pending_page(
        tenant_id=tenant_id,
        owner_user_id=caller_actor,
        agent_ids=[agent_id] if agent_id is not None else None,
        kind=kind,
        route=_route_scope(request, channel, bot_id, external_user_id, external_conversation_id),
        page=page,
        page_size=page_size,
    )
    increment(TASK_QUERY_ONLY_METRIC)
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
    increment(TASK_QUERY_ONLY_METRIC)
    return ok(request.app.state.message_catalog, badges.model_dump(mode="json"))
