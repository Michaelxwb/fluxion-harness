from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor, AuditService
from ..application.auth_service import AuthService
from ..application.dto import AccountCreateRequest, ConsoleAccountInfo
from ..infrastructure.db import get_session
from ..infrastructure.models.auth import ConsoleAccount
from .deps import AccountTenantId, CurrentAccount, get_source_ip

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]
# RULE-api-002：创建类 POST 支持 `Idempotency-Key`（可选）
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)]

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


def _account_payload(account: ConsoleAccount) -> dict[str, Any]:
    return ConsoleAccountInfo.model_validate(account).model_dump(mode="json")


@router.get("")
async def list_accounts(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    accounts, total = await AuthService(session, tenant_id=tenant_id).list_accounts(
        page=page, page_size=page_size
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[_account_payload(account) for account in accounts],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("")
async def create_account(
    payload: AccountCreateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    idempotency_key: IdempotencyKey = None,
) -> ApiResponse[Any]:
    created, replayed = await AuthService(session, tenant_id=tenant_id).create_account(
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        role=payload.role,
        idempotency_key=idempotency_key,
    )
    if not replayed:
        # FEAT-08 / RULE-10：审计与业务变更共用同一 session（同一事务），actor 为**创建者**。
        # RULE-api-002：幂等重放没有产生新变更，故不写新审计。
        await AuditService(session).record_config_change(
            tenant_id=tenant_id,
            actor=AuditActor(account_id=account.id, source_ip=get_source_ip(request)),
            resource_type="CONSOLE_ACCOUNT",
            resource_id=created.id,
            action="CREATE",
            before=None,
            after={
                "username": created.username,
                "display_name": created.display_name,
                "role": created.role,
            },
        )
    return ok(request.app.state.message_catalog, _account_payload(created))
