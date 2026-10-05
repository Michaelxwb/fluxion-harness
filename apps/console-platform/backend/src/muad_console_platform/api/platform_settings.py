"""平台设置 Console API-01..API-05（design §3.4；TASK-004）。

- 用户态租户一律取登录账号（`AccountTenantId`），**不信任** `X-Tenant-Id` 请求头。
- 读写/历史/回滚进 admin 组（`api/router.py`），已认证限额进 authenticated 组；
  组依赖已含 `require_admin` + `require_csrf`。
- 错误码只来自 `config/api-messages.yaml`：本模块只抛 `AppError`，`msg`/`http_status`
  由目录决定（`RULE-api-001`）。领域异常（TASK-003 的 service）在此映射为统一错误码，
  不让它们冒成 500。
- 保存/回滚支持 `Idempotency-Key`（`RULE-api-002`）：规范化 JSON 指纹含 endpoint 与 tenant_id。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, AppError, ErrorCode, inc_counter, ok, paginate
from muad_common import SharedSettings
from muad_contracts.platform_settings import PlatformSettingsError, parse_platform_settings
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.audit_service import AuditActor
from ..application.platform_settings_catalog import (
    build_groups,
    count_compaction_overrides,
    readonly_notes,
)
from ..application.platform_settings_guard import reject_secret_keys, validation_detail
from ..application.platform_settings_idempotency import (
    ENDPOINT_RESTORE,
    ENDPOINT_SAVE,
    PlatformSettingIdempotencyStore,
    idempotency_fingerprint,
)
from ..application.platform_settings_service import (
    PlatformSettingsModelNotFound,
    PlatformSettingsRevisionNotFound,
    PlatformSettingsService,
    PlatformSettingsVersionConflict,
)
from ..infrastructure.db import get_session
from ..infrastructure.models.auth import ConsoleAccount
from ..infrastructure.skill_validator import ENTRY_LIMIT, UNPACKED_BYTES_LIMIT, ZIP_BYTES_LIMIT
from ..metrics import PLATFORM_SETTINGS_SAVE_METRIC
from .deps import AccountTenantId, AdminAccount, CurrentAccount, get_source_ip

logger = logging.getLogger(__name__)

TenantId = AccountTenantId
Session = Annotated[AsyncSession, Depends(get_session)]

#: API-02/04 都进 admin 组，共享 `/api/v1/platform-settings` 前缀。
admin_router = APIRouter(prefix="/api/v1/platform-settings", tags=["platform-settings"])
#: API-05 只要求已认证（普通使用者也要拿到非敏感限额）。
router = APIRouter(prefix="/api/v1", tags=["platform-settings"])

SAVE_OK = "ok"
SAVE_VALIDATION_FAILED = "validation_failed"
SAVE_CONFLICT = "conflict"

#: API-05 非敏感限额（不含密钥、路径或内部地址）。
SKILL_IMPORT_LIMITS = {
    "zip_bytes_limit": ZIP_BYTES_LIMIT,
    "unpacked_bytes_limit": UNPACKED_BYTES_LIMIT,
    "entry_limit": ENTRY_LIMIT,
}
ATTACHMENT_LIMITS = {"max_bytes": 50 * 1024 * 1024, "max_per_message": 5}


class PlatformSettingsSaveRequest(BaseModel):
    """API-02 请求体：客户端读到的版本 + 整份设置文档。"""

    revision: int
    settings: dict[str, Any]


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


def _record_save(result: str) -> None:
    inc_counter(PLATFORM_SETTINGS_SAVE_METRIC, 1, {"result": result})


def _validation_error(error: PlatformSettingsError) -> AppError:
    return AppError(ErrorCode.VALIDATION_FAILED, data={"details": [validation_detail(error)]})


def _parse_settings(document: dict[str, Any]) -> Any:
    try:
        return parse_platform_settings(
            document, batch_platform_limit=SharedSettings().batch_platform_limit
        )
    except PlatformSettingsError as error:
        _record_save(SAVE_VALIDATION_FAILED)
        raise _validation_error(error) from error


async def _display_names(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    unique = [value for value in dict.fromkeys(ids) if value is not None]
    if not unique:
        return {}
    rows = await session.execute(
        select(ConsoleAccount.id, ConsoleAccount.display_name).where(
            ConsoleAccount.id.in_(unique)
        )
    )
    return {row.id: row.display_name for row in rows}


# --------------------------------------------------------------------------- API-01
@admin_router.get("")
async def read_platform_settings(
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    snapshot = await PlatformSettingsService(session).read_current(tenant_id)
    updated_by: str | None = None
    if snapshot.actor_user_id is not None:
        updated_by = (await _display_names(session, [snapshot.actor_user_id])).get(
            snapshot.actor_user_id
        )
    overrides = await count_compaction_overrides(session, tenant_id)
    data = {
        "revision": snapshot.revision,
        "updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else None,
        "updated_by": updated_by,
        "groups": build_groups(snapshot.settings, compaction_overrides=overrides),
        "readonly_notes": readonly_notes(),
    }
    return ok(request.app.state.message_catalog, data)


# --------------------------------------------------------------------------- API-02
async def _apply_save(
    session: AsyncSession,
    tenant_id: str,
    actor: AuditActor,
    payload: PlatformSettingsSaveRequest,
    idempotency_key: str | None,
) -> dict[str, Any]:
    settings = _parse_settings(payload.settings)
    fingerprint = idempotency_fingerprint(
        tenant_id,
        ENDPOINT_SAVE,
        {"revision": payload.revision, "settings": payload.settings},
    )
    store = PlatformSettingIdempotencyStore(session)
    if idempotency_key:
        await store.lock(tenant_id, idempotency_key, ENDPOINT_SAVE)
        replayed = await store.replay(tenant_id, idempotency_key, ENDPOINT_SAVE, fingerprint)
        if replayed is not None:
            return replayed
    try:
        snapshot = await PlatformSettingsService(session).save(
            tenant_id, actor, payload.revision, settings
        )
    except PlatformSettingsVersionConflict as error:
        _record_save(SAVE_CONFLICT)
        raise AppError(ErrorCode.PLATFORM_SETTINGS_VERSION_CONFLICT) from error
    except PlatformSettingsModelNotFound as error:
        _record_save(SAVE_VALIDATION_FAILED)
        raise AppError(ErrorCode.MODEL_NOT_FOUND) from error
    except PlatformSettingsError as error:
        _record_save(SAVE_VALIDATION_FAILED)
        raise _validation_error(error) from error
    data = {"revision": snapshot.revision, "settings": asdict(snapshot.settings)}
    if idempotency_key:
        await store.record(tenant_id, idempotency_key, ENDPOINT_SAVE, fingerprint, data)
    _record_save(SAVE_OK)
    logger.info(
        "platform_settings_saved revision=%s actor=%s", snapshot.revision, actor.account_id
    )
    return data


@admin_router.put("")
async def save_platform_settings(
    payload: PlatformSettingsSaveRequest,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    reject_secret_keys(payload.settings)
    data = await _apply_save(session, tenant_id, _actor(account, request), payload, idempotency_key)
    return ok(request.app.state.message_catalog, data)


# --------------------------------------------------------------------------- API-03
@admin_router.get("/revisions")
async def list_platform_settings_revisions(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await PlatformSettingsService(session).list_revisions(
        tenant_id, page, page_size
    )
    names = await _display_names(
        session, [item.actor_user_id for item in items if item.actor_user_id is not None]
    )
    data = paginate(
        items=[
            {
                "revision": item.revision,
                "created_at": item.updated_at.isoformat() if item.updated_at else None,
                "actor_display": names.get(item.actor_user_id) if item.actor_user_id else None,
                "changed_keys": list(item.changed_keys),
            }
            for item in items
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
    return ok(request.app.state.message_catalog, data)


# --------------------------------------------------------------------------- API-04
async def _apply_restore(
    session: AsyncSession,
    tenant_id: str,
    actor: AuditActor,
    target_revision: int,
    idempotency_key: str | None,
) -> dict[str, Any]:
    fingerprint = idempotency_fingerprint(
        tenant_id, ENDPOINT_RESTORE, {"revision": target_revision}
    )
    store = PlatformSettingIdempotencyStore(session)
    if idempotency_key:
        await store.lock(tenant_id, idempotency_key, ENDPOINT_RESTORE)
        replayed = await store.replay(
            tenant_id, idempotency_key, ENDPOINT_RESTORE, fingerprint
        )
        if replayed is not None:
            return replayed
    try:
        snapshot = await PlatformSettingsService(session).restore(
            tenant_id, actor, target_revision
        )
    except PlatformSettingsRevisionNotFound as error:
        raise AppError(ErrorCode.PLATFORM_SETTINGS_REVISION_NOT_FOUND) from error
    except PlatformSettingsVersionConflict as error:
        _record_save(SAVE_CONFLICT)
        raise AppError(ErrorCode.PLATFORM_SETTINGS_VERSION_CONFLICT) from error
    except PlatformSettingsError as error:
        # 历史内容早于现行 schema：按当前 schema 拒绝回滚并说明字段
        _record_save(SAVE_VALIDATION_FAILED)
        raise _validation_error(error) from error
    data = {"revision": snapshot.revision, "restored_from": target_revision}
    if idempotency_key:
        await store.record(tenant_id, idempotency_key, ENDPOINT_RESTORE, fingerprint, data)
    _record_save(SAVE_OK)
    logger.info(
        "platform_settings_restored revision=%s restored_from=%s actor=%s",
        snapshot.revision,
        target_revision,
        actor.account_id,
    )
    return data


@admin_router.post("/revisions/{revision}/restore")
async def restore_platform_settings(
    revision: int,
    request: Request,
    account: AdminAccount,
    tenant_id: TenantId,
    session: Session,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    data = await _apply_restore(
        session, tenant_id, _actor(account, request), revision, idempotency_key
    )
    return ok(request.app.state.message_catalog, data)


# --------------------------------------------------------------------------- API-05
@router.get("/platform-limits")
async def read_platform_limits(request: Request) -> ApiResponse[Any]:
    """已认证平台限额：仅非敏感容量上限（供前端复用，替代前端副本常量）。"""
    data = {"skill_import": dict(SKILL_IMPORT_LIMITS), "attachments": dict(ATTACHMENT_LIMITS)}
    return ok(request.app.state.message_catalog, data)
