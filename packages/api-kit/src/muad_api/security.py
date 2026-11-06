from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable, Collection
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Header, Request
from muad_common import process_settings

from .error_codes import ErrorCode
from .errors import AppError

SESSION_COOKIE = "muad_session"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "bearer "
INTERNAL_SERVICE_HEADER = "X-Internal-Service"


def _expected_internal_service_token(request: Request) -> str | None:
    """启动快照优先：lifespan 把 `SharedSettings` 冻结到 `app.state.settings`。

    未跑 lifespan 的测试/直构应用没有该快照，回落到进程级 `process_settings()`
    （同样只在启动期构造一次；测试 fixture 会在改 env 后清缓存）。
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        return getattr(settings, "internal_service_token", None)
    return process_settings().internal_service_token


async def require_internal_service(
    request: Request,
    x_internal_service: Annotated[str | None, Header()] = None,
) -> None:
    """内部凭据类端点只允许受信服务身份；缺失/错误身份一律 FORBIDDEN。

    与 09/08 的 Admin API 同一口径：`INTERNAL_SERVICE_TOKEN` 未配置时也一律拒绝，
    避免"未配置即放行"的默认放行面。

    比较在事件循环内本地完成（异步依赖不占线程池），Token 来自启动缓存；
    使用 `hmac.compare_digest` 做常量时间比较。
    """
    expected = _expected_internal_service_token(request)
    provided = x_internal_service or ""
    if (
        not expected
        or not provided
        or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise AppError(ErrorCode.FORBIDDEN)


InternalServiceDep = Annotated[None, Depends(require_internal_service)]


class SessionVerifier(Protocol):
    async def verify(self, session_token: str) -> Any | None: ...


class RoleResolver(Protocol):
    async def roles_for(self, principal: Any) -> Collection[str]: ...


def install_console_security(
    app: FastAPI,
    session_verifier: SessionVerifier,
    role_resolver: RoleResolver,
) -> None:
    app.state.session_verifier = session_verifier
    app.state.role_resolver = role_resolver


def _extract_session_token(request: Request) -> str:
    header = request.headers.get(AUTHORIZATION_HEADER, "")
    if header.lower().startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX) :].strip()
    return request.cookies.get(SESSION_COOKIE, "")


async def require_session(request: Request) -> Any:
    cached = getattr(request.state, "verified_session_principal", None)
    if cached is not None:
        return cached
    verifier = getattr(request.app.state, "session_verifier", None)
    resolver = getattr(request.app.state, "role_resolver", None)
    if verifier is None or resolver is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    token = _extract_session_token(request)
    principal = await verifier.verify(token) if token else None
    if principal is None:
        raise AppError(ErrorCode.UNAUTHORIZED)
    request.state.principal = principal
    request.state.roles = frozenset(await resolver.roles_for(principal))
    request.state.verified_session_principal = principal
    return principal


def require_roles(*required: str) -> Callable[[Request], Awaitable[Any]]:
    async def dependency(request: Request) -> Any:
        principal = await require_session(request)
        if required and not (request.state.roles & set(required)):
            raise AppError(ErrorCode.FORBIDDEN)
        return principal

    return dependency
