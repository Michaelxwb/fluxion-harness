from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from muad_api import ApiResponse, ok
from muad_common import SharedSettings
from muad_contracts import RunRequest

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


def _sse_response(service: RunService, run: RunStart, request: Request) -> StreamingResponse:
    frames = iter_sse_frames(
        run.events,
        run_id=run.run_id,
        heartbeat_sec=SharedSettings().run_event_heartbeat_sec,
    )
    return StreamingResponse(frames, media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.post("/runs")
async def create_run(
    payload: RunRequest,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
    idempotency_key: IdempotencyKey = None,
) -> StreamingResponse:
    run = await service.start(payload, tenant_id, idempotency_key=idempotency_key)
    return _sse_response(service, run, request)


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
        idempotency_key=idempotency_key,
        input_id=payload.input.id,
    )
    return _sse_response(service, run, request)


@router.post("/runs/cancel-active")
async def cancel_active_run(
    payload: CancelActiveRequest,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
) -> ApiResponse[Any]:
    run = await service.cancel_active(payload.agent_id, payload.platform_user_id, tenant_id)
    return ok(request.app.state.message_catalog, run_cancel_view(run))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
) -> ApiResponse[Any]:
    run = await service.cancel_run(run_id, tenant_id)
    return ok(
        request.app.state.message_catalog,
        run_cancel_view(run, include_cancel_requested=True),
    )


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
) -> ApiResponse[Any]:
    run, snapshot = await service.get_run(run_id, tenant_id)
    return ok(request.app.state.message_catalog, run_view(run, snapshot))


@router.post("/conversations")
async def create_conversation(
    payload: CreateConversationRequest,
    request: Request,
    tenant_id: TenantDep,
    service: RunServiceDep,
) -> ApiResponse[Any]:
    conversation = await service.create_conversation(
        payload.agent_id,
        payload.platform_user_id,
        tenant_id,
    )
    return ok(request.app.state.message_catalog, conversation_view(conversation))
