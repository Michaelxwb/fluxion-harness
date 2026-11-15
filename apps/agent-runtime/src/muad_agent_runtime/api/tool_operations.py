"""API-30：Task-owned ASYNC 操作的提交 / 查找 / 结果读取 / 取消。

只有受信内部身份可达（Worker 以服务身份调用）；归属按 `task.task_execution` 只读复核
（租户不符 404、`X-Actor-User-Id` 不符 403），请求体不是信任来源。本端口不创建 Run，
也不执行工具：Runtime 只持有 operation 身份与结果收件箱。

结果读取可重放——没有“消费确认”端点：消费水位（checkpoint / wait_payload / task_event）由
Worker 自己的事务写，重复读取返回同一事实，确认丢失只会重投，不会提前丢结果。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    TaskToolOperationCancelRequest,
    TaskToolOperationCancelResponse,
    TaskToolOperationList,
    TaskToolOperationSubmitRequest,
    TaskToolOperationView,
)

from ..application.async_tools import task_operations
from .deps import SessionDep, TenantDep
from .model_capacity import require_actor_user_id

router = APIRouter(
    prefix="/internal/tool-operations",
    tags=["tool-operations"],
    dependencies=[Depends(require_internal_service)],
)

ActorDep = Annotated[uuid.UUID, Depends(require_actor_user_id)]


@router.post("")
async def submit_operation(
    body: TaskToolOperationSubmitRequest,
    request: Request,
    session: SessionDep,
    tenant: TenantDep,
    actor: ActorDep,
) -> ApiResponse[TaskToolOperationView]:
    view = await task_operations.submit(
        session, tenant_id=tenant, actor_user_id=actor, request=body
    )
    await session.commit()
    return ok(request.app.state.message_catalog, view)


@router.get("")
async def list_operations(
    request: Request,
    session: SessionDep,
    tenant: TenantDep,
    actor: ActorDep,
    task_execution_id: Annotated[uuid.UUID, Query()],
    ready: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ApiResponse[TaskToolOperationList]:
    items, total = await task_operations.list_operations(
        session,
        tenant_id=tenant,
        actor_user_id=actor,
        task_execution_id=task_execution_id,
        ready=ready,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    await session.commit()
    return ok(
        request.app.state.message_catalog,
        TaskToolOperationList(items=items, page=page, page_size=page_size, total=total),
    )


@router.get("/{operation_id}")
async def get_operation(
    operation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    tenant: TenantDep,
    actor: ActorDep,
) -> ApiResponse[TaskToolOperationView]:
    view = await task_operations.get_operation(
        session, tenant_id=tenant, actor_user_id=actor, operation_id=operation_id
    )
    await session.commit()
    return ok(request.app.state.message_catalog, view)


@router.post("/cancel")
async def cancel_operation(
    body: TaskToolOperationCancelRequest,
    request: Request,
    session: SessionDep,
    tenant: TenantDep,
    actor: ActorDep,
) -> ApiResponse[TaskToolOperationCancelResponse]:
    response = await task_operations.cancel(
        session, tenant_id=tenant, actor_user_id=actor, request=body
    )
    await session.commit()
    return ok(request.app.state.message_catalog, response)
