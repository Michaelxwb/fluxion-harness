"""审计查询 API（API-01 列表、API-02 详情、API-05 导出创建、API-06 导出状态/下载）。"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_export_service import AuditExportService
from ..application.audit_query_service import AuditQueryService
from ..application.dto import AuditExportCreateRequest
from ..infrastructure.db import get_session
from ..infrastructure.repositories.audit_query_repository import AuditQueryFilters
from .deps import CurrentAccount, get_tenant_id

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


@router.post("/exports")
async def create_audit_export(
    request: Request,
    tenant_id: TenantId,
    account: CurrentAccount,
    session: Session,
    payload: AuditExportCreateRequest,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> ApiResponse[Any]:
    """API-05：创建导出任务（RULE-09 幂等，重放首次结果）。

    必须声明在 `/{audit_id}` 之前：FastAPI 按声明顺序匹配，`/exports` 否则会被详情路由吞掉。
    """
    catalog = request.app.state.message_catalog
    data = await AuditExportService(session, catalog.codes()).create_export(
        tenant_id, account.id, payload, idempotency_key
    )
    return ok(catalog, data)


@router.get("/exports/{export_id}")
async def get_audit_export_status(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    export_id: uuid.UUID,
) -> ApiResponse[Any]:
    """API-06：导出状态查询。

    必须声明在 `/{audit_id}` 之前（同上，路由按声明顺序匹配）。
    顺带在请求内惰性驱动本租户的待处理导出，使创建后的任务可轮询到终态而无需外部改库。
    """
    catalog = request.app.state.message_catalog
    service = AuditExportService(session, catalog.codes())
    await service.run_pending_exports(tenant_id)
    return ok(catalog, await service.get_export_status(tenant_id, export_id))


@router.get("/exports/{export_id}/download")
async def download_audit_export(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    export_id: uuid.UUID,
) -> Response:
    """API-06：下载导出产物；未完成 `COMMON_CONFLICT`、不存在/跨租户 `COMMON_NOT_FOUND`。"""
    catalog = request.app.state.message_catalog
    artifact = await AuditExportService(session, catalog.codes()).get_export_download(
        tenant_id, export_id
    )
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
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
