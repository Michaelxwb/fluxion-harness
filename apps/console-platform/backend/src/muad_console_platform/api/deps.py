from typing import Annotated, Any, cast

import redis.asyncio as redis
from fastapi import Depends, Request
from muad_api import AppError, require_roles, require_session
from muad_api.context import current_tenant_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_platform_sdk import PlatformAdapterRegistry, RedisPlatformSessionInvalidator

from ..application.auth_service import AuthService
from ..application.mcp_ports import McpCatalogCache, NullMcpCatalogCache
from ..application.platform_adapter_service import build_default_registry
from ..application.platform_ports import NullPlatformSessionInvalidator, PlatformSessionInvalidator
from ..infrastructure.db import get_session_factory
from ..infrastructure.mcp_catalog_cache import RedisMcpCatalogCache
from ..infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount


class ConsoleSessionVerifier:
    """把 api-kit 的 `SessionVerifier` 协议适配到 Console 的账号/会话业务。

    会话校验原语（取 token、UNAUTHORIZED/FORBIDDEN 语义）由 api-kit 提供；
    本类只负责"token → ConsoleAccount"这一段业务，并持有自己的短事务提交滑动续期。
    """

    async def verify(self, session_token: str) -> ConsoleAccount | None:
        tenant_id = current_tenant_id() or SharedSettings().default_tenant_id
        try:
            async with get_session_factory()() as session:
                account = await AuthService(session, tenant_id=tenant_id).resolve_session(session_token)
                await session.commit()
        except AppError:
            return None
        return account


class ConsoleRoleResolver:
    """把 Console 的单一 `role` 字段适配成 api-kit 需要的角色集合。"""

    async def roles_for(self, principal: Any) -> tuple[str, ...]:
        role = getattr(principal, "role", None)
        return (role,) if isinstance(role, str) else ()


def get_tenant_id() -> str:
    return current_tenant_id() or SharedSettings().default_tenant_id


def get_source_ip(request: Request) -> str | None:
    client = request.client
    return client.host if client is not None else None


async def get_current_account(principal: Annotated[Any, Depends(require_session)]) -> ConsoleAccount:
    if not isinstance(principal, ConsoleAccount):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return principal


CurrentAccount = Annotated[ConsoleAccount, Depends(get_current_account)]


async def require_admin(
    principal: Annotated[Any, Depends(require_roles(ROLE_ADMIN))],
) -> ConsoleAccount:
    if not isinstance(principal, ConsoleAccount):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return principal


AdminAccount = Annotated[ConsoleAccount, Depends(require_admin)]


def get_adapter_registry(request: Request) -> "PlatformAdapterRegistry":
    registry = getattr(request.app.state, "platform_adapters", None)
    if registry is None:
        registry = build_default_registry()
        request.app.state.platform_adapters = registry
    return registry


def get_platform_sessions(request: Request) -> PlatformSessionInvalidator:
    sessions = getattr(request.app.state, "platform_sessions", None)
    if sessions is not None:
        return cast(PlatformSessionInvalidator, sessions)
    redis_url = SharedSettings().redis_url
    if not redis_url:
        invalidator: PlatformSessionInvalidator = NullPlatformSessionInvalidator()
    else:
        client = redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
        request.app.state.platform_sessions_client = client
        invalidator = RedisPlatformSessionInvalidator(client)
    request.app.state.platform_sessions = invalidator
    return invalidator


def get_mcp_catalog_cache(request: Request) -> McpCatalogCache:
    cache = getattr(request.app.state, "mcp_catalog_cache", None)
    if cache is not None:
        return cast(McpCatalogCache, cache)
    redis_url = SharedSettings().redis_url
    if not redis_url:
        resolved: McpCatalogCache = NullMcpCatalogCache()
    else:
        client = redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
        request.app.state.mcp_catalog_cache_client = client
        resolved = RedisMcpCatalogCache(client)
    request.app.state.mcp_catalog_cache = resolved
    return resolved
