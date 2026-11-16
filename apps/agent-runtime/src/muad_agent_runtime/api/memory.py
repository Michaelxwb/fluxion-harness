"""Protected Console administrative transport; Runtime stays the only writer."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from muad_api import ApiResponse, AppError, InternalServiceDep, ok, paginate
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import MemoryCandidate, RememberMemoryInput
from muad_contracts.memory_admin import MemoryCommandRequest, MemoryStatus
from pydantic import JsonValue, ValidationError

from ..application.memory_admin_queries import MemoryAdminQueries
from ..application.memory_commands import MemoryWriteContext
from ..application.memory_service import MemoryService
from .deps import CredentialCanariesDep, SessionDep, TenantDep

router = APIRouter(prefix="/internal/admin/users/{user_id}/memory", tags=["admin-memory"])
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
ActorId = Annotated[UUID, Header(alias="X-Actor-Id")]
Endpoint = "memory.admin"


def _context(tenant: str, user_id: UUID, actor: UUID, canaries: tuple[str, ...]) -> MemoryWriteContext:
    return MemoryWriteContext(
        tenant_id=tenant,
        user_id=user_id,
        actor_type="ADMIN",
        actor_id=str(actor),
        credential_canaries=canaries,
    )


def _candidate(payload: dict[str, JsonValue] | None) -> MemoryCandidate:
    if payload is None:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    try:
        return RememberMemoryInput.model_validate(payload).normalized(taxonomy_version=1)
    except ValidationError:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from None


@router.get("")
async def list_memory(
    user_id: UUID,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    memory_kind: str | None = None,
    predicate: str | None = None,
    status: MemoryStatus | None = None,
    include_deleted: bool = False,
) -> ApiResponse[dict[str, JsonValue]]:
    items, total = await MemoryAdminQueries(session).list_entries(
        tenant,
        user_id,
        page=page,
        page_size=page_size,
        memory_kind=memory_kind,
        predicate=predicate,
        status=status,
        include_deleted=include_deleted,
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


@router.get("/conflicts")
async def list_conflicts(
    user_id: UUID,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[dict[str, JsonValue]]:
    items, total = await MemoryAdminQueries(session).list_conflicts(
        tenant, user_id, page=page, page_size=page_size
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


@router.get("/{memory_id}/history")
async def history(
    user_id: UUID,
    memory_id: UUID,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[dict[str, JsonValue]]:
    items, total = await MemoryAdminQueries(session).history(
        tenant, user_id, memory_id, page=page, page_size=page_size
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


@router.post("/commands")
async def command(
    user_id: UUID,
    body: MemoryCommandRequest,
    request: Request,
    tenant: TenantDep,
    _session: SessionDep,
    _guard: InternalServiceDep,
    canaries: CredentialCanariesDep,
    key: CommandKey,
) -> ApiResponse[dict[str, JsonValue]]:
    if body.actor_id is None:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    ctx = _context(tenant, user_id, body.actor_id, canaries)
    service = MemoryService()
    if body.action == "CLEAR":
        outcome = await service.clear_memories(ctx, command_id=key, endpoint=Endpoint)
    elif body.action == "DELETE":
        if body.memory_id is None or body.expected_version is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        outcome = await service.delete_memory(
            ctx,
            body.memory_id,
            expected_version=body.expected_version,
            command_id=key,
            endpoint=Endpoint,
        )
    elif body.action == "RESTORE":
        if body.memory_id is None or body.expected_version is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        outcome = await service.restore_memory(
            ctx, body.memory_id, expected_version=body.expected_version
        )
    elif body.action in ("ENABLE", "DISABLE"):
        if body.memory_id is None or body.expected_version is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        outcome = await service.set_memory_enabled(
            ctx,
            body.memory_id,
            expected_version=body.expected_version,
            enabled=body.action == "ENABLE",
        )
    elif body.action == "RESOLVE":
        if body.candidate_id is None or body.expected_version is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        outcome = await service.resolve_candidate(
            ctx, body.candidate_id, expected_version=body.expected_version
        )
    elif body.action == "REJECT":
        if body.candidate_id is None or body.expected_version is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        outcome = await service.reject_candidate(
            ctx, body.candidate_id, expected_version=body.expected_version
        )
    elif body.action == "UPDATE":
        if body.memory_id is None or body.expected_version is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        outcome = await service.patch_memory(
            ctx,
            body.memory_id,
            expected_version=body.expected_version,
            updates=dict(body.updates or {}),
        )
    elif body.action == "CREATE":
        outcome = await service.create_memory(
            ctx, _candidate(body.candidate), command_id=key, endpoint=Endpoint
        )
    else:
        outcome = await service.apply_candidate(
            ctx, _candidate(body.candidate), command_id=key, endpoint=Endpoint
        )
    return ok(request.app.state.message_catalog, asdict(outcome))


@router.delete("")
async def clear(
    user_id: UUID,
    request: Request,
    tenant: TenantDep,
    _session: SessionDep,
    _guard: InternalServiceDep,
    canaries: CredentialCanariesDep,
    key: CommandKey,
    actor: ActorId,
) -> ApiResponse[dict[str, JsonValue]]:
    outcome = await MemoryService().clear_memories(
        _context(tenant, user_id, actor, canaries), command_id=key, endpoint=f"{Endpoint}.clear"
    )
    return ok(request.app.state.message_catalog, asdict(outcome))
