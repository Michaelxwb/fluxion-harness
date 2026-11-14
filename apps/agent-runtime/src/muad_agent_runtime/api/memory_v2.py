"""Protected Console v2 memory endpoints; Runtime remains the only writer."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from muad_api import ApiResponse, InternalServiceDep, ok, paginate
from muad_contracts.memory import RememberMemoryInputV2
from muad_contracts.memory_admin import MemoryV2AdminItem, MemoryV2CommandRequest

from ..application.memory_commands import MemoryWriteContext
from ..application.memory_queries import MemoryQueries, MemoryReadScope
from ..application.memory_service import MemoryService
from .deps import CredentialCanariesDep, SessionDep, TenantDep

router = APIRouter(prefix="/internal/admin/users/{user_id}/memory/v2", tags=["admin-memory-v2"])
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
ActorId = Annotated[UUID, Header(alias="X-Actor-Id")]


def _status(row) -> str:
    from datetime import UTC, datetime

    if not row.enabled:
        return "DISABLED"
    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
        return "EXPIRED"
    return "ACTIVE"


def _item(row) -> MemoryV2AdminItem:
    return MemoryV2AdminItem(
        id=row.id,
        memory_key=row.memory_key,
        memory_kind=row.memory_kind,  # type: ignore[arg-type]
        predicate=row.predicate,
        scope_state=row.scope_state,  # type: ignore[arg-type]
        scope=row.scope,
        content=row.content,
        title=row.title,
        summary=row.summary,
        tags=list(row.tags),
        cardinality=row.cardinality,  # type: ignore[arg-type]
        priority=row.priority,  # type: ignore[arg-type]
        author=row.author,  # type: ignore[arg-type]
        authority=row.authority,
        version=row.version,
        enabled=row.enabled,
        expires_at=row.expires_at,
        last_confirmed_at=row.last_confirmed_at,
        status=_status(row),
        create_time=row.last_confirmed_at or row.expires_at or __import__("datetime").datetime.now(__import__("datetime").UTC),
        update_time=row.last_confirmed_at or row.expires_at or __import__("datetime").datetime.now(__import__("datetime").UTC),
    )


@router.get("")
async def list_v2(
    user_id: UUID,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[dict[str, object]]:
    snapshot = await MemoryQueries().read_snapshot_v2(MemoryReadScope(tenant_id=tenant, user_id=user_id))
    items = [_item(row) for row in snapshot.rows]
    offset = (page - 1) * page_size
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[item.model_dump(mode="json") for item in items[offset : offset + page_size]],
            page=page,
            page_size=page_size,
            total=len(items),
        ),
    )


@router.post("/commands")
async def command_v2(
    user_id: UUID,
    body: MemoryV2CommandRequest,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    canaries: CredentialCanariesDep,
    key: CommandKey,
) -> ApiResponse[dict[str, object]]:
    ctx = MemoryWriteContext(
        tenant_id=tenant,
        user_id=user_id,
        actor_type="ADMIN",
        actor_id=str(body.actor_id),
        credential_canaries=canaries,
    )
    candidate = None
    if body.action in ("CREATE", "UPDATE") and body.candidate is not None:
        candidate = RememberMemoryInputV2.model_validate(body.candidate).normalized(taxonomy_version=1)
    service = MemoryService()
    if body.action == "CLEAR":
        outcome = await service.clear_memories(ctx, command_id=key, endpoint="memory.admin.v2")
    elif body.action == "DELETE":
        outcome = await service.delete_memory(
            ctx,
            body.memory_id,
            expected_version=body.expected_version,
            command_id=key,
            endpoint="memory.admin.v2",
        )
    elif body.action == "RESTORE" and body.memory_id is not None and body.expected_version is not None:
        outcome = await service.restore_memory(
            ctx, body.memory_id, expected_version=body.expected_version
        )
    elif body.action in ("ENABLE", "DISABLE") and body.memory_id is not None and body.expected_version is not None:
        outcome = await service.set_memory_enabled(
            ctx,
            body.memory_id,
            expected_version=body.expected_version,
            enabled=body.action == "ENABLE",
        )
    elif body.action == "RESOLVE" and body.candidate_id is not None and body.expected_version is not None:
        outcome = await service.resolve_candidate(
            ctx, body.candidate_id, expected_version=body.expected_version
        )
    else:
        outcome = await service.apply_candidate(
            ctx, candidate, command_id=key, endpoint="memory.admin.v2"
        )
    return ok(request.app.state.message_catalog, outcome.__dict__)


@router.post("/conflicts/{candidate_id}/resolve")
async def resolve_v2(
    user_id: UUID,
    candidate_id: UUID,
    request: Request,
    tenant: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    canaries: CredentialCanariesDep,
    actor: ActorId,
    expected_version: Annotated[int, Query(ge=1)],
) -> ApiResponse[dict[str, object]]:
    ctx = MemoryWriteContext(
        tenant_id=tenant, user_id=user_id, actor_type="ADMIN", actor_id=str(actor), credential_canaries=canaries
    )
    outcome = await MemoryService().resolve_candidate(
        ctx, candidate_id, expected_version=expected_version
    )
    return ok(request.app.state.message_catalog, outcome.__dict__)
