"""消息级投递受理端点（design §4.6）：Worker 把 `task.message_outbox` 交给 Runtime。

只对受信服务身份开放；租户随请求体与 `X-Tenant-Id` 一致。端点只做边界校验与幂等入队，
真实发送仍由 `MessageDeliveryPump` 按租约完成——outbox 是**受理**回执，不是送达回执。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_contracts import MessageDeliveryEnqueueRequest

from ..application.message_delivery_registration import enqueue_task_message_delivery
from .deps import SessionDep, TenantDep

router = APIRouter(
    prefix="/internal/message-deliveries",
    tags=["message-deliveries"],
    dependencies=[Depends(require_internal_service)],
)


@router.post("")
async def enqueue_message_delivery(
    body: MessageDeliveryEnqueueRequest,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
) -> ApiResponse[Any]:
    """受理一条消息：同 `(message_key, target_key)` 返回首次结果，不重复发送。"""
    receipt = await enqueue_task_message_delivery(
        session, tenant_id=tenant_id, request=body
    )
    await session.commit()
    return ok(request.app.state.message_catalog, receipt.model_dump(mode="json"))
