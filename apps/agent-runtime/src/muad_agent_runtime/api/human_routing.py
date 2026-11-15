"""聊天入站消息的路由裁决（design §3.2.6）：先判是不是 Human 回答，再决定是否新建 Run。

只由 IM Gateway 以受信服务身份调用：actor/agent 来自 Console 对入站消息的核验，来源
（channel/bot/user/会话）原样透传。端点**不写**任何业务状态——唯一的写是 Owner 的回答台账，
且只在"恰好一个 OPEN 问题"时发生。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_api.context import current_trace_id
from muad_api.security import require_internal_service
from muad_contracts import ChannelHumanRouteRequest

from ..application.human_routing import HumanRouteOwnerPort, HumanRoutingService
from .deps import TenantDep, get_human_route_owner

router = APIRouter(
    prefix="/v1",
    tags=["human-routing"],
    dependencies=[Depends(require_internal_service)],
)

HumanRouteOwnerDep = Annotated[HumanRouteOwnerPort, Depends(get_human_route_owner)]


@router.post("/human-routing")
async def resolve_human_route(
    payload: ChannelHumanRouteRequest,
    request: Request,
    tenant_id: TenantDep,
    owner: HumanRouteOwnerDep,
) -> ApiResponse[Any]:
    """NONE 才允许继续建 Run；ANSWERED/CLARIFY/REJECTED 都已经有确定的用户可见结论。"""
    decision = await HumanRoutingService(owner).route(
        payload, tenant_id=tenant_id, trace_id=current_trace_id()
    )
    return ok(request.app.state.message_catalog, decision.as_response().model_dump(mode="json"))
