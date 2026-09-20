"""审计查询 API（Console 最近运行/配置变更审计）。"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditService
from ..infrastructure.db import get_session
from .deps import get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/audits", tags=["audits"])


@router.get("")
async def list_audits(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    resource_id: uuid.UUID | None = Query(default=None),  # noqa: B008
    resource_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await AuditService(session).list_audits(
        tenant_id,
        resource_id=resource_id,
        resource_type=resource_type,
        page=page,
        page_size=page_size,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )
