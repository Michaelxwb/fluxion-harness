import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.channel_service import ChannelService
from ..application.dto import (
    AgentGrantItem,
    BindCodeCreateResponse,
    IdentityItem,
    MemoryItem,
    UserCreateRequest,
    UserDetail,
    UserListItem,
    UserUpdateRequest,
)
from ..application.grant_service import GrantService
from ..application.memory_service import MemoryService
from ..application.user_service import UserService
from ..infrastructure.db import get_session
from ..infrastructure.models.control import PlatformUser
from .deps import CurrentAccount, get_source_ip, get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/users", tags=["users"])

_ZERO_COUNTS = {"agent_grant_count": 0, "credential_count": 0, "identity_count": 0, "memory_count": 0}


def _item(user: PlatformUser, counts: dict[str, int]) -> dict[str, Any]:
    return UserListItem(
        id=user.id,
        user_code=user.user_code,
        display_name=user.display_name,
        status=user.status,
        create_time=user.create_time,
        update_time=user.update_time,
        **counts,
    ).model_dump(mode="json")


def _detail(user: PlatformUser, counts: dict[str, int]) -> dict[str, Any]:
    return UserDetail(
        id=user.id,
        tenant_id=user.tenant_id,
        user_code=user.user_code,
        display_name=user.display_name,
        status=user.status,
        metadata=user.metadata_json,
        create_time=user.create_time,
        update_time=user.update_time,
        **counts,
    ).model_dump(mode="json")


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


@router.get("")
async def list_users(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, pattern="^(ACTIVE|DISABLED)$"),
) -> ApiResponse[Any]:
    users, stats, total = await UserService(session).list_users(tenant_id, page, page_size, keyword, status)
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[_item(user, stats[user.id]) for user in users],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("")
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    user = await UserService(session).create_user(tenant_id, payload, _actor(account, request))
    return ok(request.app.state.message_catalog, _detail(user, _ZERO_COUNTS))


@router.get("/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    user, counts = await UserService(session).get_user(tenant_id, user_id)
    return ok(request.app.state.message_catalog, _detail(user, counts))


@router.get("/{user_id}/agents")
async def list_user_agents(
    user_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await UserService(session).list_agent_grants(tenant_id, user_id, page, page_size)
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[AgentGrantItem(**item).model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.get("/{user_id}/memory")
async def list_user_memory(
    user_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category: str | None = Query(default=None, pattern="^(PREFERENCE|WORK_STYLE|EXPLICIT)$"),
) -> ApiResponse[Any]:
    items, total = await UserService(session).list_memories(tenant_id, user_id, page, page_size, category)
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[MemoryItem(**item).model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("/{user_id}/agents/{agent_id}")
async def grant_agent(
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    grant = await GrantService(session).grant(
        tenant_id, user_id, agent_id, account.id, _actor(account, request)
    )
    return ok(
        request.app.state.message_catalog,
        {
            "user_id": str(grant.user_id),
            "agent_id": str(grant.agent_id),
            "granted_at": grant.granted_at.isoformat(),
            "granted_by": str(grant.granted_by),
        },
    )


@router.delete("/{user_id}/agents/{agent_id}")
async def revoke_agent(
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await GrantService(session).revoke(tenant_id, user_id, agent_id, _actor(account, request))
    return ok(
        request.app.state.message_catalog,
        {"user_id": str(user_id), "agent_id": str(agent_id), "revoked": True},
    )


@router.delete("/{user_id}/memory/{memory_id}")
async def delete_user_memory(
    user_id: uuid.UUID,
    memory_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await MemoryService(session).delete(tenant_id, user_id, memory_id, _actor(account, request))
    return ok(
        request.app.state.message_catalog,
        {"memory_id": str(memory_id), "deleted": True},
    )


@router.delete("/{user_id}/memory")
async def clear_user_memory(
    user_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    deleted_count = await MemoryService(session).delete_all(tenant_id, user_id, _actor(account, request))
    return ok(request.app.state.message_catalog, {"deleted_count": deleted_count})


@router.post("/{user_id}/bind-codes")
async def create_bind_code(
    user_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    code, expires_at = await ChannelService(session).create_bind_code(
        tenant_id, user_id, _actor(account, request)
    )
    payload = BindCodeCreateResponse(bind_code=code, expires_at=expires_at, status="ACTIVE")
    return ok(request.app.state.message_catalog, payload.model_dump(mode="json"))


@router.get("/{user_id}/identities")
async def list_user_identities(
    user_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    channel: str | None = Query(default=None, max_length=32),
) -> ApiResponse[Any]:
    items, total = await ChannelService(session).list_identities(
        tenant_id, user_id, page, page_size, channel
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[IdentityItem(**item).model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.delete("/{user_id}/identities/{identity_id}")
async def unbind_identity(
    user_id: uuid.UUID,
    identity_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await ChannelService(session).unbind_identity(
        tenant_id, user_id, identity_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, {"identity_id": str(identity_id), "unbound": True})


@router.put("/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    user = await UserService(session).update_user(tenant_id, user_id, payload, _actor(account, request))
    _, counts = await UserService(session).get_user(tenant_id, user.id)
    return ok(request.app.state.message_catalog, _detail(user, counts))
