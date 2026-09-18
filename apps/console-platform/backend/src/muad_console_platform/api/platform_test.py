import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_platform_sdk import PlatformAdapterRegistry
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.platform_test_service import PlatformTestService
from ..infrastructure.db import get_session
from .deps import CurrentAccount, get_adapter_registry, get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Registry = Annotated[PlatformAdapterRegistry, Depends(get_adapter_registry)]

router = APIRouter(prefix="/api/v1/project-platforms", tags=["project-platforms"])


class PlatformTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_user_id: uuid.UUID | None = None
    timeout_ms: int = Field(default=3000, ge=100, le=60000)


@router.post("/{platform_id}/test")
async def test_platform(
    platform_id: uuid.UUID,
    payload: PlatformTestRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    registry: Registry,
) -> ApiResponse[Any]:
    del account
    return ok(
        request.app.state.message_catalog,
        await PlatformTestService(session, registry).test_platform(
            tenant_id,
            platform_id,
            test_user_id=payload.test_user_id,
            timeout_ms=payload.timeout_ms,
        ),
    )
