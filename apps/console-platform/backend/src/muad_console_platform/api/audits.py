"""审计查询 API（Console 聚合审计列表 API-01 与审计详情 API-02）。"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_query_service import AuditQueryService
from ..infrastructure.db import get_session
from ..infrastructure.repositories.audit_query_repository import AuditQueryFilters
from .deps import get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/audits", tags=["audits"])


@router.get("")
async def list_audits(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    audit_type: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: uuid.UUID | None = Query(default=None),  # noqa: B008
    actor_user_id: uuid.UUID | None = Query(default=None),  # noqa: B008
    action: str | None = Query(default=None, max_length=64),
    result_status: str | None = Query(default=None, max_length=64),
    trace_id: str | None = Query(default=None, max_length=64),
    start_time: datetime | None = Query(default=None),  # noqa: B008
    end_time: datetime | None = Query(default=None),  # noqa: B008
    keyword: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    filters = AuditQueryFilters(
        audit_type=audit_type,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=actor_user_id,
        action=action,
        result_status=result_status,
        trace_id=trace_id,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
    )
    catalog = request.app.state.message_catalog
    items, total = await AuditQueryService(session, catalog.codes()).list_audits(
        tenant_id, filters, page=page, page_size=page_size
    )
    return ok(
        catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )


@router.get("/{audit_id}")
async def get_audit_detail(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    audit_id: uuid.UUID,
    audit_type: str = Query(description="来源表：CONFIG/TOOL/EGRESS/MODEL"),
) -> ApiResponse[Any]:
    """审计详情：UUID 跨 4 张来源表不互通，`audit_type` 必填且不猜表。"""
    catalog = request.app.state.message_catalog
    data = await AuditQueryService(session, catalog.codes()).get_audit_detail(
        tenant_id, audit_id, audit_type
    )
    return ok(catalog, data)
