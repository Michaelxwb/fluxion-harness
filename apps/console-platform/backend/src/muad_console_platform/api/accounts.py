from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.auth_service import AuthService
from ..application.dto import AccountCreateRequest, ConsoleAccountInfo
from ..infrastructure.db import get_session
from ..infrastructure.models.auth import ConsoleAccount
from .deps import get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


def _account_payload(account: ConsoleAccount) -> dict[str, Any]:
    return ConsoleAccountInfo.model_validate(account).model_dump(mode="json")


@router.get("")
async def list_accounts(
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    accounts = await AuthService(session, tenant_id=tenant_id).list_accounts()
    return ok(request.app.state.message_catalog, [_account_payload(account) for account in accounts])


@router.post("")
async def create_account(
    payload: AccountCreateRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    account = await AuthService(session, tenant_id=tenant_id).create_account(
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        role=payload.role,
    )
    return ok(request.app.state.message_catalog, _account_payload(account))
