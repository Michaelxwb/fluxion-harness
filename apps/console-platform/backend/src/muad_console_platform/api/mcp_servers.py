"""MCP Server 管理 API（API-01~API-06；discover/tools 在 TASK-004，users 在 TASK-005）。"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.dto import McpCreateRequest, McpUpdateRequest
from ..application.mcp_service import McpService
from ..infrastructure.db import get_session
from .deps import AdminAccount, CurrentAccount, get_source_ip, get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp"])


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


@router.get("")
async def list_servers(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    user_scope: Literal["ALL", "SELECTED"] | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    connection_status: str | None = Query(default=None),
) -> ApiResponse[Any]:
    items, total = await McpService(session).list_servers(
        tenant_id,
        page,
        page_size,
        keyword=keyword,
        user_scope=user_scope,
        enabled=enabled,
        connection_status=connection_status,
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=[item.model_dump(mode="json") for item in items], page=page, page_size=page_size, total=total),
    )


@router.post("")
async def create_server(
    payload: McpCreateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    data = await McpService(session).create_server(
        tenant_id, payload, _actor(account, request), idempotency_key=idempotency_key
    )
    return ok(request.app.state.message_catalog, data)


@router.get("/{mcp_id}")
async def get_server(
    mcp_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    detail = await McpService(session).get_server_detail(tenant_id, mcp_id)
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.put("/{mcp_id}")
async def update_server(
    mcp_id: uuid.UUID,
    payload: McpUpdateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    detail = await McpService(session).update_server(
        tenant_id, mcp_id, payload, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, detail.model_dump(mode="json"))


@router.delete("/{mcp_id}")
async def delete_server(
    mcp_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await McpService(session).delete_server(tenant_id, mcp_id, _actor(account, request))
    return ok(request.app.state.message_catalog, {})


@router.post("/{mcp_id}/test")
async def test_connection(
    mcp_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    timeout_ms: Annotated[int | None, Query(ge=100, le=60000)] = None,
) -> ApiResponse[Any]:
    data = await McpService(session).test_connection(tenant_id, mcp_id, timeout_ms)
    return ok(request.app.state.message_catalog, data)


@router.post("/{mcp_id}/discover-tools")
async def discover_tools(
    mcp_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await McpService(session).discover_tools(tenant_id, mcp_id)
    return ok(request.app.state.message_catalog, data)


@router.get("/{mcp_id}/tools")
async def list_tools(
    mcp_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await McpService(session).list_tools(tenant_id, mcp_id, page, page_size)
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )


@router.get("/{mcp_id}/tools/{tool_name}")
async def get_tool(
    mcp_id: uuid.UUID,
    tool_name: str,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await McpService(session).get_tool(tenant_id, mcp_id, tool_name)
    return ok(request.app.state.message_catalog, data)
