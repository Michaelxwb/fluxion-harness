"""Protected Console administrative transport; public/model inputs cannot pick authority."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from muad_api import ApiResponse, InternalServiceDep, ok, paginate
from muad_contracts.memory import MemoryAuthor, MemoryCategory
from muad_contracts.memory_admin import MemoryAdminMutation, MemoryAdminRequest, MemoryStatus
from pydantic import JsonValue

from ..application.memory_admin import MemoryAdminService
from ..application.memory_admin_queries import MemoryAdminQueries
from ..application.run_service import execution_defaults_from_platform
from ..infrastructure.db import get_session as get_session
from .deps import CredentialCanariesDep, SessionDep, TenantDep, get_platform_settings_client

router = APIRouter(prefix="/internal/admin/users/{user_id}/memory", tags=["admin-memory"])
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


@router.post("/commands")
async def command(
    user_id: UUID,
    body: MemoryAdminRequest,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    canaries: CredentialCanariesDep,
    key: CommandKey,
) -> ApiResponse[dict[str, JsonValue]]:
    snapshot = await get_platform_settings_client(request).fetch_snapshot(tenant_id=tenant)
    policy = execution_defaults_from_platform(snapshot.settings).memory_policy
    result = await MemoryAdminService(session, policy, canaries).execute(
        tenant, user_id, body, command_id=key
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.get("")
async def list_memory(
    user_id: UUID,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    category: MemoryCategory | None = None,
    author: MemoryAuthor | None = None,
    status: MemoryStatus | None = None,
) -> ApiResponse[dict[str, JsonValue]]:
    items, total = await MemoryAdminQueries(session).list_entries(
        tenant, user_id, page=page, page_size=page_size, category=category, author=author, status=status
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


@router.get("/{memory_key}/history")
async def history(
    user_id: UUID,
    memory_key: str,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[dict[str, JsonValue]]:
    items, total = await MemoryAdminQueries(session).history(
        tenant, user_id, memory_key, page=page, page_size=page_size
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


@router.delete("")
async def clear(
    user_id: UUID,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    canaries: CredentialCanariesDep,
    key: CommandKey,
    actor: Annotated[UUID, Header(alias="X-Actor-Id")],
) -> ApiResponse[dict[str, JsonValue]]:
    snapshot = await get_platform_settings_client(request).fetch_snapshot(tenant_id=tenant)
    policy = execution_defaults_from_platform(snapshot.settings).memory_policy
    body = MemoryAdminRequest(actor_id=actor, mutation=MemoryAdminMutation(action="CLEAR"))
    result = await MemoryAdminService(session, policy, canaries).execute(
        tenant, user_id, body, command_id=key, endpoint="memory.admin.clear"
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))
