"""Trusted, durable terminal result ingress; no model execution in the HTTP handler."""

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_contracts import ToolResultReceipt, ToolResultRequest

from ..application.tool_result_receipts import receive_result
from .deps import SessionDep, TenantDep

router = APIRouter(
    prefix="/internal/tool-results", tags=["tool-results"], dependencies=[Depends(require_internal_service)]
)


@router.post("")
async def tool_result(
    body: ToolResultRequest, request: Request, session: SessionDep, tenant: TenantDep
) -> ApiResponse[ToolResultReceipt]:
    receipt = await receive_result(session, tenant, body)
    await session.commit()
    return ok(request.app.state.message_catalog, receipt)
