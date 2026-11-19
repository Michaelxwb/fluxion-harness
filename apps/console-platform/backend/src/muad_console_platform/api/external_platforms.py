"""外部平台管理面（API-17、API-19）。

只读平台元数据与入口候选。平台元数据**不包含任何验签因子**（RULE-secret-002）：
管理员能看到「是否已配置」「身份键有哪些」，看不到公钥/salt/issuer。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.dto import ExternalEntryItem, ExternalPlatformItem
from ..application.external_identity_service import ExternalIdentityService
from ..infrastructure.db import get_session
from .deps import AccountTenantId, AdminAccount

Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/external-platforms", tags=["external-platforms"])


@router.get("", response_model=ApiResponse[list[ExternalPlatformItem]])
async def list_external_platforms(
    _admin: AdminAccount,
    session: Session,
    request: Request,
) -> ApiResponse[list[ExternalPlatformItem]]:
    """API-17：已配置的外部平台（当前只有 `mssw`）。"""
    service = ExternalIdentityService(session)
    return ok(request.app.state.message_catalog, service.list_platforms())


@router.get(
    "/{platform_key}/entries",
    response_model=ApiResponse[list[ExternalEntryItem]],
)
async def list_platform_entries(
    platform_key: str,
    _admin: AdminAccount,
    tenant_id: AccountTenantId,
    session: Session,
    request: Request,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    identity_id: Annotated[uuid.UUID | None, Query()] = None,
    query: Annotated[str, Query(max_length=128)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[list[ExternalEntryItem]]:
    """API-19：可绑定入口。服务端分页与过滤。

    `granted` 表示是否已授权：传 `identity_id` 时只看该身份，否则看该用户名下任一身份。
    授权弹窗按身份操作，必须传 `identity_id`，否则撤销按钮的判定会跨身份串味。
    """
    service = ExternalIdentityService(session)
    items = await service.list_entries(
        tenant_id=tenant_id,
        platform_key=platform_key,
        user_id=user_id,
        identity_id=identity_id,
        query=query,
        page=page,
        page_size=page_size,
    )
    return ok(request.app.state.message_catalog, items)
