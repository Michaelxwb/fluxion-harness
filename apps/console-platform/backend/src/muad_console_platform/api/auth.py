from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from muad_api import ApiResponse, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.auth_service import AuthService
from ..application.dto import ConsoleAccountInfo, LoginRequest, PasswordChangeRequest
from ..infrastructure.db import get_session
from ..infrastructure.models.auth import ConsoleAccount
from .deps import CurrentAccount, get_source_ip, get_tenant_id
from .security import SESSION_COOKIE, clear_auth_cookies, new_csrf_token, set_auth_cookies

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

public_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _account_payload(account: ConsoleAccount) -> dict[str, Any]:
    return ConsoleAccountInfo.model_validate(account).model_dump(mode="json")


@public_router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session,
) -> ApiResponse[Any]:
    tenant_id = request.headers.get("X-Tenant-Id") or None
    account, token = await AuthService(session, tenant_id=tenant_id).login(
        payload.username,
        payload.password,
        get_source_ip(request),
    )
    set_auth_cookies(response, token, new_csrf_token())
    return ok(request.app.state.message_catalog, _account_payload(account))


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    await AuthService(session, tenant_id=tenant_id).logout(token)
    clear_auth_cookies(response)
    return ok(request.app.state.message_catalog, {"logged_out": True})


@router.get("/me")
async def me(request: Request, account: CurrentAccount) -> ApiResponse[Any]:
    return ok(request.app.state.message_catalog, _account_payload(account))


@router.post("/password")
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await AuthService(session, tenant_id=tenant_id).change_password(
        account,
        payload.current_password,
        payload.new_password,
    )
    return ok(request.app.state.message_catalog, {"changed": True})
