import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, ok, paginate
from muad_contracts.memory import MemoryAuthor, MemoryCategory, MemoryKey
from muad_contracts.memory_admin import MemoryStatus
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.channel_service import ChannelService
from ..application.dto import (
    AgentGrantItem,
    BindCodeCreateResponse,
    IdentityItem,
    MemoryCreateRequest,
    MemoryEnabledUpdateRequest,
    MemoryHistoryItem,
    MemoryItem,
    MemoryRestoreRequest,
    UserBasic,
    UserCreateRequest,
    UserDetail,
    UserListItem,
    UserUpdateRequest,
)
from ..application.grant_service import GrantService
from ..application.memory_service import MemoryAdminService, MemoryService
from ..application.user_service import UserService
from ..infrastructure.db import get_session
from ..infrastructure.models.control import PlatformUser
from .deps import AccountTenantId, CurrentAccount, RuntimeMemoryClientDep, get_source_ip

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]
#: 记忆命令的幂等键 = Runtime 命令号（design §3.7「Idempotency-Key 必填」）。
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
ExpectedVersion = Annotated[int, Query(ge=1)]

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _basic(user: PlatformUser) -> dict[str, Any]:
    return UserBasic(
        id=user.id,
        user_code=user.user_code,
        display_name=user.display_name,
        status=user.status,
        metadata=user.metadata_json,
        create_time=user.create_time,
        update_time=user.update_time,
    ).model_dump(mode="json")


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
    return ok(request.app.state.message_catalog, _basic(user))


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
    # 过滤枚举直接取共享契约，避免在 Console 复制字面量；`Annotated[..., Query()]` 形态
    # 与仓库 lint 口径一致（B008 不允许把 `Query(...)` 调用放进参数默认值）。
    category: Annotated[MemoryCategory | None, Query()] = None,
    author: Annotated[MemoryAuthor | None, Query()] = None,
    status: Annotated[MemoryStatus | None, Query()] = None,
) -> ApiResponse[Any]:
    """按 author/category/status 过滤；status 由 enabled + expires_at 派生，已删除不出现在列表。"""
    items, total = await MemoryService(session).list_by_user(
        tenant_id,
        user_id,
        page,
        page_size,
        category=category.value if category is not None else None,
        author=author.value if author is not None else None,
        status=status,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[MemoryItem(**item).model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.get("/{user_id}/memory/{memory_key}/history")
async def get_user_memory_history(
    user_id: uuid.UUID,
    memory_key: MemoryKey,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    """只读版本历史，按 version 倒序；已删除的记忆其历史仍可查，未知 key 返回 MEMORY_NOT_FOUND。"""
    items, total = await MemoryService(session).history(tenant_id, user_id, memory_key, page, page_size)
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[MemoryHistoryItem(**item).model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("/{user_id}/memory")
async def create_user_memory(
    user_id: uuid.UUID,
    payload: MemoryCreateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    runtime: RuntimeMemoryClientDep,
    idempotency_key: CommandKey,
) -> ApiResponse[Any]:
    """管理员人工新增/显式覆盖（design §3.7）：author 不由调用方选，版本冲突不覆盖。

    重放语义与回执：同一 `Idempotency-Key` 再次请求返回 Runtime 首次落库的回执，不产生第二次效果。
    """
    result = await MemoryAdminService(session, runtime).create(
        tenant_id, user_id, _actor(account, request), idempotency_key, payload
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.patch("/{user_id}/memory/{memory_key}")
async def update_user_memory(
    user_id: uuid.UUID,
    memory_key: MemoryKey,
    payload: MemoryEnabledUpdateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    runtime: RuntimeMemoryClientDep,
    idempotency_key: CommandKey,
) -> ApiResponse[Any]:
    """启禁用：`expected_version` 必填（冲突不覆盖）；过期条目启用必须显式给新有效期。"""
    result = await MemoryAdminService(session, runtime).set_enabled(
        tenant_id, user_id, _actor(account, request), idempotency_key, memory_key, payload
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


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


@router.post("/{user_id}/memory/{memory_key}/restore")
async def restore_user_memory(
    user_id: uuid.UUID,
    memory_key: MemoryKey,
    payload: MemoryRestoreRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    runtime: RuntimeMemoryClientDep,
    idempotency_key: CommandKey,
) -> ApiResponse[Any]:
    """恢复删除墓碑：按 tombstone 的**保留版本**恢复（identity/version 不重置）。

    版本由界面从 tombstone 的版本历史读到（与删除同口径）：创建路由不传 `expected_version`，
    对已删除的 key 直接 UPSERT 必然 409；版本冲突由 Runtime 明确拒绝，刷新后重试。
    """
    result = await MemoryAdminService(session, runtime).restore(
        tenant_id, user_id, _actor(account, request), idempotency_key, memory_key, payload
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.delete("/{user_id}/memory/{memory_id}")
async def delete_user_memory(
    user_id: uuid.UUID,
    memory_id: uuid.UUID,
    expected_version: ExpectedVersion,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    runtime: RuntimeMemoryClientDep,
    idempotency_key: CommandKey,
) -> ApiResponse[Any]:
    """按 id 删除：Runtime 保留 identity/history（tombstone）；`expected_version` 必填。"""
    result = await MemoryAdminService(session, runtime).delete(
        tenant_id,
        user_id,
        _actor(account, request),
        idempotency_key,
        memory_id,
        expected_version,
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.delete("/{user_id}/memory")
async def clear_user_memory(
    user_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    runtime: RuntimeMemoryClientDep,
    idempotency_key: CommandKey,
) -> ApiResponse[Any]:
    """一命令原子清空并保留逐项历史；Runtime 递增 forget_epoch 撤销未消费提议。"""
    result = await MemoryAdminService(session, runtime).clear(
        tenant_id, user_id, _actor(account, request), idempotency_key
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


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
    return ok(request.app.state.message_catalog, _basic(user))
