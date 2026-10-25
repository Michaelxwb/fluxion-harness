from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_common import SharedSettings
from muad_contracts import RunRequest
from muad_contracts.webchat import (
    ADMISSION_CONVERSATION_ID_HEADER,
    ADMISSION_EXECUTION_STATUS_HEADER,
    ADMISSION_RESUMED_HEADER,
    ADMISSION_RUN_ID_HEADER,
    ADMISSION_RUN_STATUS_HEADER,
)

from ..application.dto import (
    CancelActiveRequest,
    CreateConversationRequest,
    ResumeRunRequest,
    conversation_view,
    run_cancel_view,
    run_view,
)
from ..application.run_service import RunService, RunStart
from ..application.sse import SSE_HEADERS, SSE_MEDIA_TYPE, iter_sse_frames
from .deps import RunServiceDep, TenantDep

router = APIRouter(prefix="/v1", tags=["runs"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


async def _sse_response(
    service: RunService, run: RunStart, request: Request
) -> StreamingResponse:
    frames = iter_sse_frames(
        run.events,
        run_id=run.run_id,
        heartbeat_sec=SharedSettings().run_event_heartbeat_sec,
    )
    headers = dict(SSE_HEADERS)
    headers[ADMISSION_RUN_ID_HEADER] = str(run.run_id)
    headers[ADMISSION_CONVERSATION_ID_HEADER] = str(run.conversation_id)
    headers[ADMISSION_RESUMED_HEADER] = "1" if run.resumed else "0"
    headers[ADMISSION_RUN_STATUS_HEADER] = str(run.status)
    if run.execution_status is not None:
        headers[ADMISSION_EXECUTION_STATUS_HEADER] = json.dumps(
            run.execution_status.model_dump(mode="json"), ensure_ascii=True
        )
    # 关键：StreamingResponse 会活到流结束；先把请求级 Session 归还连接池，
    # 否则 1000 条流 = 1000 条 idle-in-transaction 连接。
    await service.release_session()
    return StreamingResponse(frames, media_type=SSE_MEDIA_TYPE, headers=headers)


@router.post("/runs")
async def create_run(
    payload: RunRequest,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
    idempotency_key: IdempotencyKey = None,
) -> StreamingResponse:
    run = await service.start(payload, tenant_id, idempotency_key=idempotency_key)
    return await _sse_response(service, run, request)


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: uuid.UUID,
    payload: ResumeRunRequest,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
    idempotency_key: IdempotencyKey = None,
) -> StreamingResponse:
    run = await service.resume(
        run_id,
        payload.input.text,
        tenant_id,
        platform_user_id=payload.platform_user_id,
        idempotency_key=idempotency_key,
        input_id=payload.input.id,
        confirmation=payload.confirmation_binding(),
    )
    return await _sse_response(service, run, request)


@router.post("/runs/cancel-active")
async def cancel_active_run(
    payload: CancelActiveRequest,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
) -> ApiResponse[Any]:
    """停止命令：`source` 给出会话或入口+外线线程时只取消该来源的活跃 Run（RISK-F03）。"""
    run = await service.cancel_active(
        payload.agent_id, payload.platform_user_id, tenant_id, source=payload.source
    )
    return ok(request.app.state.message_catalog, run_cancel_view(run))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
) -> ApiResponse[Any]:
    """API-08：取消**这一个** Run（网页停止按原 Run 定位）；可重放，终态重放不再产生副作用。"""
    run = await service.cancel_run(run_id, tenant_id)
    return ok(
        request.app.state.message_catalog,
        run_cancel_view(run, include_cancel_requested=True),
    )


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """API-04：按已确认 canonical seq 重放/续跟既有 Run 的持久事件（SSE 重连，不重新执行）。"""
    run = await service.tail_run_events(run_id, tenant_id, after_seq=after_seq)
    return await _sse_response(service, run, request)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
) -> ApiResponse[Any]:
    run, snapshot, wait = await service.get_run(run_id, tenant_id)
    return ok(request.app.state.message_catalog, run_view(run, snapshot, wait))


@router.get(
    "/runs/{run_id}/scope", dependencies=[Depends(require_internal_service)]
)
async def get_run_scope(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
    user_id: Annotated[uuid.UUID, Query()],
) -> ApiResponse[Any]:
    """API-07 打开流的单次归属解析：Run 必须属于该主体，会话入口/Agent 由 Run 行导出。"""
    data = await service.get_run_scope(run_id, tenant_id, user_id=user_id)
    return ok(request.app.state.message_catalog, data)


@router.post("/conversations")
async def create_conversation(
    payload: CreateConversationRequest,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
    idempotency_key: IdempotencyKey = None,
) -> ApiResponse[Any]:
    conversation = await service.create_conversation(
        payload.agent_id,
        payload.platform_user_id,
        tenant_id,
        idempotency_key=idempotency_key,
        entry_id=payload.entry_id,
        external_thread_id=payload.external_thread_id,
    )
    return ok(request.app.state.message_catalog, conversation_view(conversation))
