from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from typing import Any, Protocol

from fastapi import FastAPI, Request

from .error_codes import ErrorCode
from .errors import AppError

SESSION_COOKIE = "muad_session"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "bearer "


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
    return principal


def require_roles(*required: str) -> Callable[[Request], Awaitable[Any]]:
    async def dependency(request: Request) -> Any:
        principal = await require_session(request)
        if required and not (request.state.roles & set(required)):
            raise AppError(ErrorCode.FORBIDDEN)
        return principal

    return dependency
