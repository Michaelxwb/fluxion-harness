from typing import Annotated, Any, cast

import redis.asyncio as redis
from fastapi import Depends, Request
from muad_api import AppError, require_roles, require_session
from muad_api.context import current_tenant_id
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_platform_sdk import PlatformAdapterRegistry, RedisPlatformSessionInvalidator

from ..application.artifact_fetch_service import ArtifactFetchService, ArtifactResolvePort
from ..application.artifact_fetch_tokens import ArtifactFetchTokens, configured_ttl_sec
from ..application.auth_service import AuthService
from ..application.mcp_ports import McpCatalogCache, NullMcpCatalogCache
from ..application.platform_adapter_service import build_default_registry
from ..application.platform_ports import NullPlatformSessionInvalidator, PlatformSessionInvalidator
from ..infrastructure.db import get_session_factory
from ..infrastructure.mcp_catalog_cache import RedisMcpCatalogCache
from ..infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from ..infrastructure.worker_client import WorkerAdminClient


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


def get_header_tenant_id() -> str:
    """请求头派生的租户——**只给内部服务与公开入口用**，不要在用户态路由上使用。

    租户是**已认证主体**的属性，不是客户端可声明的属性：用户态路由一律用 `AccountTenantId`。
    保留本依赖只覆盖两处无主体可依的场景：`/internal/*`（另有服务身份门控）与公开登录
    （无会话，只能靠头按租户定位账号）。见 `HeaderTenantId`。
    """
    return current_tenant_id() or SharedSettings().default_tenant_id


def get_source_ip(request: Request) -> str | None:
    client = request.client
    return client.host if client is not None else None


async def get_current_account(principal: Annotated[Any, Depends(require_session)]) -> ConsoleAccount:
    if not isinstance(principal, ConsoleAccount):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return principal


CurrentAccount = Annotated[ConsoleAccount, Depends(get_current_account)]


def get_account_tenant_id(account: CurrentAccount) -> str:
    """以登录账号所属租户为准，不信任浏览器可改写的 `X-Tenant-Id` 请求头。"""
    return account.tenant_id


AccountTenantId = Annotated[str, Depends(get_account_tenant_id)]

# 用户态/管理态路由的**默认**租户依赖就是账号租户：默认安全，用错需要显式 opt-in。
# 头派生版本另起名（`HeaderTenantId`），只允许内部服务与公开入口导入。
TenantId = AccountTenantId
HeaderTenantId = Annotated[str, Depends(get_header_tenant_id)]


async def require_admin(
    principal: Annotated[Any, Depends(require_roles(ROLE_ADMIN))],
) -> ConsoleAccount:
    if not isinstance(principal, ConsoleAccount):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return principal


AdminAccount = Annotated[ConsoleAccount, Depends(require_admin)]


def get_worker_client(request: Request) -> WorkerAdminClient:
    """Console → Worker Admin API 的共享客户端（按进程缓存，测试可覆盖）。"""
    client = getattr(request.app.state, "worker_client", None)
    if client is None:
        settings = SharedSettings()
        client = WorkerAdminClient(
            settings.agent_worker_url, service_token=settings.internal_service_token
        )
        request.app.state.worker_client = client
    return client


WorkerClient = Annotated[WorkerAdminClient, Depends(get_worker_client)]


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


def get_artifact_fetch_tokens(request: Request) -> ArtifactFetchTokens:
    """取件令牌的签发/兑换器（按进程缓存，测试可覆盖）。

    **进程内**：多实例部署下 A 实例签的链接到 B 实例会 404。这是设计中接受的代价——
    TTL 只有几分钟，且**失败方向是拒绝**，不会误放行（见 `artifact_fetch_tokens` 模块说明）。
    """
    tokens = getattr(request.app.state, "artifact_fetch_tokens", None)
    if tokens is None:
        tokens = ArtifactFetchTokens(ttl_sec=configured_ttl_sec())
        request.app.state.artifact_fetch_tokens = tokens
    return tokens


def get_artifact_fetch_service(request: Request) -> ArtifactFetchService:
    """取件服务（按进程缓存）：真实共享 store + 指向 runtime 的**解析单点**。

    Console 只碰 `control` schema，不直读 `runtime.artifact`（仓库里没有这种先例，且那是
    **跨 schema 的表结构耦合**：runtime 改一列，取件会静默退化）。归属与元信息一律问 runtime。
    """
    service = getattr(request.app.state, "artifact_fetch_service", None)
    if service is None:
        settings = SharedSettings()
        service = ArtifactFetchService(
            store=NfsArtifactStore(settings.artifact_root),
            resolver=ArtifactResolvePort(
                settings.agent_runtime_url,
                service_token=settings.internal_service_token,
            ),
        )
        request.app.state.artifact_fetch_service = service
    return service


FetchTokensDep = Annotated[ArtifactFetchTokens, Depends(get_artifact_fetch_tokens)]
FetchServiceDep = Annotated[ArtifactFetchService, Depends(get_artifact_fetch_service)]
