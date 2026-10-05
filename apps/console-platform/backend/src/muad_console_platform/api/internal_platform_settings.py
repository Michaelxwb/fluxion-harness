"""API-06 内部取设置快照（design §3.4；TASK-004）。

内部端点无用户会话：租户由调用方（Runtime/Worker/Gateway，已过服务身份门控）以
`X-Tenant-Id` 声明（`HeaderTenantId`），与 `resolve-definition` 同一条通道与鉴权。

**失败即失败**：读不到就抛错（统一错误码 500），绝不返回"最后一次已知值"（RULE-06），
并记 `platform_settings_fetch_total{caller=..., result="failed"}`。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from muad_api import ApiResponse, inc_counter, ok
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.platform_settings_service import PlatformSettingsService
from ..application.runtime_credentials import require_service_identity
from ..infrastructure.db import get_session
from ..metrics import PLATFORM_SETTINGS_FETCH_METRIC
from .deps import HeaderTenantId

logger = logging.getLogger(__name__)

TenantId = HeaderTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

#: 允许的调用方标识；缺省按当前唯一消费方 Runtime 计。
CALLERS = frozenset({"runtime", "worker", "gateway"})

router = APIRouter(prefix="/internal/v1", tags=["internal-platform-settings"])


def _caller(value: str | None) -> str:
    return value if value in CALLERS else "runtime"


def _record(caller: str, result: str) -> None:
    inc_counter(PLATFORM_SETTINGS_FETCH_METRIC, 1, {"caller": caller, "result": result})


@router.get("/platform-settings")
async def internal_platform_settings(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    x_internal_service: Annotated[str | None, Header()] = None,
    x_caller_service: Annotated[str | None, Header()] = None,
) -> ApiResponse[Any]:
    require_service_identity(x_internal_service)
    caller = _caller(x_caller_service)
    try:
        snapshot = await PlatformSettingsService(session).read_current(tenant_id)
    except Exception:
        _record(caller, "failed")
        logger.warning("platform_settings_fetch_failed caller=%s", caller)
        raise
    _record(caller, "ok")
    data = {"revision": snapshot.revision, "settings": asdict(snapshot.settings)}
    return ok(request.app.state.message_catalog, data)
