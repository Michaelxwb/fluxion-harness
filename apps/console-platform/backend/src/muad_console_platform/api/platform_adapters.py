from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from muad_api import ApiResponse, AppError, ok, paginate
from muad_api.error_codes import ErrorCode
from muad_platform_sdk import PlatformAdapterNotFound, PlatformAdapterRegistry

from ..application.platform_adapter_service import adapter_metadata
from .deps import CurrentAccount, get_adapter_registry

Registry = Annotated[PlatformAdapterRegistry, Depends(get_adapter_registry)]

router = APIRouter(prefix="/api/v1/platform-adapters", tags=["platform-adapters"])


@router.get("")
async def list_platform_adapters(
    request: Request,
    account: CurrentAccount,
    registry: Registry,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
) -> ApiResponse[Any]:
    del account
    adapters = list(registry.list())
    start = (page - 1) * page_size
    items = [adapter_metadata(adapter) for adapter in adapters[start : start + page_size]]
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=len(adapters)),
    )


@router.get("/{adapter_key}")
async def get_platform_adapter(
    adapter_key: str,
    request: Request,
    account: CurrentAccount,
    registry: Registry,
) -> ApiResponse[Any]:
    del account
    try:
        adapter = registry.get(adapter_key)
    except PlatformAdapterNotFound as exc:
        raise AppError(ErrorCode.PLATFORM_ADAPTER_NOT_FOUND) from exc
    return ok(request.app.state.message_catalog, adapter_metadata(adapter))
