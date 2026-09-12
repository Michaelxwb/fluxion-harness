"""FastAPI dependencies: DB session factory and ADR-021 auth guards."""

from collections.abc import Awaitable, Callable
from functools import lru_cache

from fastapi import Header
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.auth_repository import AuthRepository
from adapters.postgres.session import create_engine_and_session_factory
from framework.settings import get_settings
from framework.web.errors import AppError
from framework.web.security import ROLE_ADMIN, SessionPrincipal


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    settings = get_settings()
    _, factory = create_engine_and_session_factory(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )
    return factory


@lru_cache
def _get_auth_repository() -> AuthRepository:
    return AuthRepository(get_session_factory())


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError(code="AUTH_REQUIRED", message="missing bearer token", status_code=401)
    return authorization.removeprefix("Bearer ").strip()


async def current_principal(
    authorization: str | None = Header(default=None),
) -> SessionPrincipal:
    """Resolve the caller from the Authorization header (RULE-API-02)."""
    return await _get_auth_repository().resolve(_bearer_token(authorization))


def require_admin() -> Callable[..., Awaitable[SessionPrincipal]]:
    """Guard for Admin-only endpoints (publish/user/credential/model/...)."""

    async def _guard(
        authorization: str | None = Header(default=None),
    ) -> SessionPrincipal:
        principal = await current_principal(authorization)
        if principal.role != ROLE_ADMIN:
            raise AppError(code="AUTH_FORBIDDEN", message="admin role required", status_code=403)
        return principal

    return _guard


def require_builder() -> Callable[..., Awaitable[SessionPrincipal]]:
    """Builder+Admin guard: every authenticated Console role may pass."""

    async def _guard(
        authorization: str | None = Header(default=None),
    ) -> SessionPrincipal:
        return await current_principal(authorization)

    return _guard
