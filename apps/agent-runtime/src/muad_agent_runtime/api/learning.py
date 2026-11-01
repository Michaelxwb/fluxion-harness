"""经验生命周期 internal API（服务身份 + 租户 + 真实 actor）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from muad_api import ApiResponse, AppError, InternalServiceDep, ok
from muad_api.error_codes import ErrorCode
from muad_contracts.learning_admin import (
    FamilyControlRequest,
    LearningActionItem,
    LearningActionRequest,
)
from pydantic import JsonValue

from ..application.learning_activation import LearningActivation
from ..application.learning_admin_service import LearningAdminService
from ..infrastructure.db import get_session_factory
from .deps import TenantDep, get_platform_settings_client

router = APIRouter(prefix="/internal/admin", tags=["admin-learning"])
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
Actor = Annotated[UUID, Header(alias="X-Actor-Id")]


def _service(request: Request) -> LearningAdminService:
    factory = get_session_factory()
    return LearningAdminService(
        factory,
        activation=LearningActivation(factory),
        settings_client=get_platform_settings_client(request),
    )


@router.post(
    "/users/{user_id}/experiences/{memory_key}/replay", status_code=202
)
async def replay(
    user_id: UUID,
    memory_key: str,
    request: Request,
    tenant: TenantDep,
    _guard: InternalServiceDep,
    key: CommandKey,
    _actor: Actor,
) -> ApiResponse[dict[str, JsonValue]]:
    job = await _service(request).enqueue_replay(tenant, user_id, memory_key, command_id=key)
    return ok(request.app.state.message_catalog, LearningAdminService.job_json(job))


@router.post("/users/{user_id}/experiences/{memory_key}/activate")
async def activate(
    user_id: UUID,
    memory_key: str,
    body: LearningActionRequest,
    request: Request,
    tenant: TenantDep,
    _guard: InternalServiceDep,
    key: CommandKey,
    actor: Actor,
) -> ApiResponse[dict[str, JsonValue]]:
    outcome = await _service(request).activate(
        tenant,
        user_id,
        memory_key,
        operator=str(actor),
        reason=body.reason,
        base_config_hash="",
    )
    return ok(
        request.app.state.message_catalog,
        LearningActionItem(
            experience_key=memory_key,
            activation_state=outcome.activation_state,
            status=outcome.status,
        ).model_dump(mode="json"),
    )


@router.post("/users/{user_id}/experiences/{memory_key}/retire")
async def retire(
    user_id: UUID,
    memory_key: str,
    body: LearningActionRequest,
    request: Request,
    tenant: TenantDep,
    _guard: InternalServiceDep,
    key: CommandKey,
    actor: Actor,
) -> ApiResponse[dict[str, JsonValue]]:
    if not body.reason:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    outcome = await _service(request).retire(
        tenant, user_id, memory_key, operator=str(actor), reason=body.reason
    )
    return ok(
        request.app.state.message_catalog,
        LearningActionItem(
            experience_key=memory_key,
            activation_state=outcome.activation_state,
            status=outcome.status,
        ).model_dump(mode="json"),
    )


@router.post("/users/{user_id}/experiences/{memory_key}/regenerate")
async def regenerate(
    user_id: UUID,
    memory_key: str,
    body: LearningActionRequest,
    request: Request,
    tenant: TenantDep,
    _guard: InternalServiceDep,
    key: CommandKey,
    actor: Actor,
) -> ApiResponse[dict[str, JsonValue]]:
    if not body.reason:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    outcome = await _service(request).regenerate(
        tenant, user_id, memory_key, operator=str(actor), reason=body.reason
    )
    return ok(
        request.app.state.message_catalog,
        LearningActionItem(
            experience_key=memory_key,
            activation_state=outcome.activation_state,
            status=outcome.status,
        ).model_dump(mode="json"),
    )


@router.get("/learning/jobs/{job_id}")
async def job_status(
    job_id: UUID,
    request: Request,
    tenant: TenantDep,
    _guard: InternalServiceDep,
) -> ApiResponse[dict[str, JsonValue]]:
    job = await _service(request).job(tenant, job_id)
    return ok(request.app.state.message_catalog, LearningAdminService.job_json(job))


@router.post("/runs/{run_id}/learning-stop")
async def stop_run(
    run_id: UUID,
    body: LearningActionRequest,
    request: Request,
    tenant: TenantDep,
    _guard: InternalServiceDep,
    actor: Actor,
) -> ApiResponse[dict[str, JsonValue]]:
    if not body.reason:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    results = await _service(request).stop_run(
        tenant, run_id, operator=str(actor), reason=body.reason
    )
    return ok(request.app.state.message_catalog, {"items": results})


@router.post("/learning/families/{family}/control")
async def family_control(
    family: str,
    body: FamilyControlRequest,
    request: Request,
    tenant: TenantDep,
    _guard: InternalServiceDep,
    actor: Actor,
) -> ApiResponse[dict[str, JsonValue]]:
    if not body.reason:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    result = await _service(request).set_family_control(
        tenant, family, paused=body.paused, reason=body.reason, actor=actor
    )
    return ok(request.app.state.message_catalog, result)
