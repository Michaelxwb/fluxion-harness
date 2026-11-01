"""经验管理只读 API（ADMIN）：双视图共用同一投影。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, AppError, ok, paginate
from muad_api.error_codes import ErrorCode
from muad_contracts.learning_admin import LearningActionRequest
from muad_contracts.memory import MemoryCategory
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.learning_service import LearningService
from ..infrastructure.db import get_session
from .deps import AccountTenantId, CurrentAccount, RuntimeLearningClientDep

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])
TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Window = Annotated[int, Query(ge=1, le=90)]
State = Annotated[str | None, Query(pattern="^(PROPOSED|TRIAL|ACTIVE|RETIRED)$")]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


@router.get("/experiences")
async def list_experiences(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    user_id: uuid.UUID,
    state: State = None,
    family: str | None = None,
    category: MemoryCategory | None = None,
    q: str | None = Query(default=None, max_length=128),
    page: Page = 1,
    page_size: PageSize = 20,
) -> ApiResponse[Any]:
    items, counts = await LearningService(session).list_experiences(
        tenant_id,
        user_id,
        state=state,
        family=family,
        category=category.value if category else None,
        query=q,
        page=page,
        page_size=page_size,
    )
    return ok(
        request.app.state.message_catalog,
        {
            **paginate(
                items=[item.model_dump(mode="json") for item in items],
                page=page,
                page_size=page_size,
                total=counts["total"],
            ),
            "counts": counts,
        },
    )


@router.get("/experiences/{user_id}/{memory_key}")
async def get_experience(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    user_id: uuid.UUID,
    memory_key: str,
) -> ApiResponse[Any]:
    detail = await LearningService(session).get_experience(tenant_id, user_id, memory_key)
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/families")
async def list_families(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    source: str | None = Query(default=None, pattern="^(skill|agent|unknown)$"),
    q: str | None = Query(default=None, max_length=128),
    window_days: Window = 7,
    page: Page = 1,
    page_size: PageSize = 20,
) -> ApiResponse[Any]:
    items, total, unclassified = await LearningService(session).list_families(
        tenant_id,
        source=source,
        query=q,
        window_days=window_days,
        page=page,
        page_size=page_size,
    )
    return ok(
        request.app.state.message_catalog,
        {
            **paginate(
                items=[item.model_dump(mode="json") for item in items],
                page=page,
                page_size=page_size,
                total=total,
            ),
            "window": f"{window_days}d",
            "unclassified_runs": unclassified,
        },
    )


@router.get("/families/{family}")
async def get_family(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    family: str,
    window_days: Window = 7,
) -> ApiResponse[Any]:
    detail = await LearningService(session).get_family(
        tenant_id, family, window_days=window_days
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.post("/experiences/{user_id}/{memory_key}/replay", status_code=202)
async def replay_experience(
    request: Request,
    tenant_id: TenantId,
    account: CurrentAccount,
    runtime: RuntimeLearningClientDep,
    user_id: uuid.UUID,
    memory_key: str,
    key: IdempotencyKey,
) -> ApiResponse[Any]:
    job = await runtime.replay(
        tenant_id=tenant_id,
        user_id=user_id,
        key=memory_key,
        actor_id=account.id,
        command_id=key,
    )
    return ok(request.app.state.message_catalog, job.model_dump(mode="json"))


@router.post("/experiences/{user_id}/{memory_key}/{action}")
async def experience_action(
    request: Request,
    tenant_id: TenantId,
    account: CurrentAccount,
    runtime: RuntimeLearningClientDep,
    user_id: uuid.UUID,
    memory_key: str,
    action: str,
    body: LearningActionRequest,
    key: IdempotencyKey,
) -> ApiResponse[Any]:
    if action not in ("activate", "retire", "regenerate"):
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if action in ("retire", "regenerate") and not body.reason:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    result = await runtime.action(
        action,
        tenant_id=tenant_id,
        user_id=user_id,
        key=memory_key,
        actor_id=account.id,
        command_id=key,
        reason=body.reason,
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.get("/jobs/{job_id}")
async def learning_job(
    request: Request,
    tenant_id: TenantId,
    runtime: RuntimeLearningClientDep,
    job_id: uuid.UUID,
) -> ApiResponse[Any]:
    job = await runtime.job(tenant_id=tenant_id, job_id=job_id)
    return ok(request.app.state.message_catalog, job.model_dump(mode="json"))


@router.get("/runs/{run_id}/diagnostics")
async def run_diagnostics(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    run_id: uuid.UUID,
) -> ApiResponse[Any]:
    data = await LearningService(session).run_diagnostics(tenant_id, run_id)
    return ok(request.app.state.message_catalog, data)
