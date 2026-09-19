import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.dto import SkillUpdateRequest, SkillUserScopeRequest
from ..application.skill_service import SkillService
from ..infrastructure.db import get_session
from ..infrastructure.skill_validator import ZIP_BYTES_LIMIT, invalid_package
from .deps import AdminAccount, CurrentAccount, get_source_ip, get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


async def _upload_bytes(file: UploadFile) -> bytes:
    data = await file.read(ZIP_BYTES_LIMIT + 1)
    if len(data) > ZIP_BYTES_LIMIT:
        raise invalid_package()
    return data


@router.get("")
async def list_skills(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user_scope: Literal["ALL", "SELECTED"] | None = Query(default=None),
    execution_mode: Literal["SYNC", "ASYNC", "AUTO"] | None = Query(default=None),
) -> ApiResponse[Any]:
    items, total = await SkillService(session).list_skills(
        tenant_id,
        page,
        page_size,
        user_scope,
        execution_mode,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[item.model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("/import")
async def import_skill(
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    file: Annotated[UploadFile, File()],
    version: Annotated[str, Form(min_length=1, max_length=64)],
    key: Annotated[str | None, Form(min_length=1, max_length=128)] = None,
    default_script: Annotated[str | None, Form(max_length=256)] = None,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    detail = await SkillService(session).import_skill(
        tenant_id,
        version=version,
        key=key,
        default_script=default_script,
        data=await _upload_bytes(file),
        actor=_actor(account, request),
        idempotency_key=idempotency_key,
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/{skill_id}")
async def get_skill(
    skill_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    detail = await SkillService(session).get_skill_detail(tenant_id, skill_id)
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.put("/{skill_id}")
async def update_skill(
    skill_id: uuid.UUID,
    payload: SkillUpdateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    detail = await SkillService(session).update_skill(
        tenant_id,
        skill_id,
        payload,
        _actor(account, request),
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/{skill_id}/artifacts")
async def list_artifacts(
    skill_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await SkillService(session).list_artifacts(tenant_id, skill_id, page, page_size)
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[item.model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("/{skill_id}/artifacts")
async def add_artifact(
    skill_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    file: Annotated[UploadFile, File()],
    version: Annotated[str, Form(min_length=1, max_length=64)],
    default_script: Annotated[str | None, Form(max_length=256)] = None,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    detail = await SkillService(session).add_artifact(
        tenant_id,
        skill_id,
        version=version,
        default_script=default_script,
        data=await _upload_bytes(file),
        actor=_actor(account, request),
        idempotency_key=idempotency_key,
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/{skill_id}/artifacts/{artifact_id}")
async def get_artifact(
    skill_id: uuid.UUID,
    artifact_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    detail = await SkillService(session).get_artifact(tenant_id, skill_id, artifact_id)
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.put("/{skill_id}/user-scope")
async def set_user_scope(
    skill_id: uuid.UUID,
    payload: SkillUserScopeRequest,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    detail = await SkillService(session).set_user_scope(
        tenant_id,
        skill_id,
        payload.user_scope,
        _actor(account, request),
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.get("/{skill_id}/users")
async def list_grants(
    skill_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    items = await SkillService(session).list_grants(tenant_id, skill_id)
    return ok(request.app.state.message_catalog, [item.model_dump(mode="json") for item in items])


@router.post("/{skill_id}/users/{user_id}")
async def add_grant(
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    item = await SkillService(session).add_grant(
        tenant_id,
        skill_id,
        user_id,
        _actor(account, request),
    )
    return ok(request.app.state.message_catalog, item.model_dump(mode="json"))


@router.delete("/{skill_id}/users/{user_id}")
async def remove_grant(
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await SkillService(session).remove_grant(tenant_id, skill_id, user_id, _actor(account, request))
    return ok(request.app.state.message_catalog, {"deleted": True})
