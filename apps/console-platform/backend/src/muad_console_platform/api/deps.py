from typing import Annotated, cast

from fastapi import Depends, Request
from muad_api import AppError
from muad_api.context import current_tenant_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_platform_sdk import PlatformAdapterRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.auth_service import AuthService
from ..application.platform_adapter_service import build_default_registry
from ..application.platform_ports import NullPlatformSessionInvalidator, PlatformSessionInvalidator
from ..infrastructure.db import get_session
from ..infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from .security import SESSION_COOKIE


def get_tenant_id() -> str:
    return current_tenant_id() or SharedSettings().default_tenant_id


def get_source_ip(request: Request) -> str | None:
    client = request.client
    return client.host if client is not None else None


async def get_current_account(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
) -> ConsoleAccount:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AppError(ErrorCode.UNAUTHORIZED)
    return await AuthService(session, tenant_id=tenant_id).resolve_session(token)


CurrentAccount = Annotated[ConsoleAccount, Depends(get_current_account)]


async def require_admin(account: CurrentAccount) -> ConsoleAccount:
    if account.role != ROLE_ADMIN:
        raise AppError(ErrorCode.FORBIDDEN)
    return account


AdminAccount = Annotated[ConsoleAccount, Depends(require_admin)]


def get_adapter_registry(request: Request) -> "PlatformAdapterRegistry":
    registry = getattr(request.app.state, "platform_adapters", None)
    if registry is None:
        registry = build_default_registry()
        request.app.state.platform_adapters = registry
    return registry


def get_platform_sessions(request: Request) -> PlatformSessionInvalidator:
    sessions = getattr(request.app.state, "platform_sessions", None)
    if sessions is None:
        return NullPlatformSessionInvalidator()
    return cast(PlatformSessionInvalidator, sessions)
