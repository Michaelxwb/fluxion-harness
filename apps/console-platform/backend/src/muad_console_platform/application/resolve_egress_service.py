"""API-08 Resolve Egress Access：平台解析、凭据选择、策略判定（受信 Runtime 调用）。"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import (
    ProjectPlatform,
    SharedCredentialRef,
    UserCredentialRef,
)
from .runtime_credentials import require_service_identity

VALID_EXECUTION_TYPES = frozenset({"RUN", "TASK"})
VALID_TARGET_TYPES = frozenset({"PLATFORM_SERVICE", "HTTP", "MCP"})


def _platform_snapshot(platform: ProjectPlatform) -> dict[str, Any]:
    return {
        "id": str(platform.id),
        "key": platform.key,
        "resolver_type": platform.resolver_type,
        "resolver_config": platform.resolver_config_json,
        "adapter_key": platform.adapter_key,
        "adapter_config": platform.adapter_config_json,
        "adapter_schema_version": platform.adapter_schema_version,
        "credential_mode": platform.credential_mode,
    }


def _credential_payload(
    ref_id: uuid.UUID, credential_json: dict[str, Any], schema_version: str
) -> dict[str, Any]:
    return {
        "ref_id": str(ref_id),
        "credential_json": credential_json,
        "credential_schema_version": schema_version,
    }


async def _resolve_credential(
    session: AsyncSession,
    tenant_id: str,
    actor_user_id: uuid.UUID,
    platform: ProjectPlatform,
) -> dict[str, Any] | None:
    mode = platform.credential_mode
    if mode == "NONE":
        return None

    async def user_row() -> UserCredentialRef | None:
        return (
            await session.execute(
                select(UserCredentialRef).where(
                    UserCredentialRef.tenant_id == tenant_id,
                    UserCredentialRef.user_id == actor_user_id,
                    UserCredentialRef.platform_id == platform.id,
                    UserCredentialRef.is_deleted.is_(False),
                    UserCredentialRef.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()

    async def shared_row() -> SharedCredentialRef | None:
        return (
            await session.execute(
                select(SharedCredentialRef).where(
                    SharedCredentialRef.tenant_id == tenant_id,
                    SharedCredentialRef.platform_id == platform.id,
                    SharedCredentialRef.is_deleted.is_(False),
                    SharedCredentialRef.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()

    if mode == "USER_ONLY":
        row = await user_row()
        if row is None:
            raise AppError(ErrorCode.CREDENTIAL_MISSING)
        return _credential_payload(row.id, row.credential_json, row.credential_schema_version)
    if mode == "SHARED_ONLY":
        row = await shared_row()
        if row is None:
            raise AppError(ErrorCode.CREDENTIAL_MISSING)
        return _credential_payload(row.id, row.credential_json, row.credential_schema_version)
    # USER_THEN_SHARED
    user = await user_row()
    if user is not None:
        return _credential_payload(user.id, user.credential_json, user.credential_schema_version)
    shared = await shared_row()
    if shared is not None:
        return _credential_payload(shared.id, shared.credential_json, shared.credential_schema_version)
    raise AppError(ErrorCode.CREDENTIAL_MISSING)


def _target_allowed(platform: ProjectPlatform, target: dict[str, Any]) -> bool:
    """HTTP target 校验：域必须落在平台 resolver/allowlist 内；平台服务调用恒 ALLOW。"""
    if target.get("type") != "HTTP":
        return True
    url = str(target.get("url") or "")
    method = str(target.get("method") or "").upper()
    if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
        return False
    host = urlparse(url).hostname or ""
    allowed_hosts: list[str] = []
    base_url = str(platform.resolver_config_json.get("base_url") or "")
    if base_url:
        base_host = urlparse(base_url).hostname
        if base_host:
            allowed_hosts.append(base_host)
    for entry in platform.adapter_config_json.get("allowlist", []) or []:
        allowed_host = urlparse(str(entry)).hostname
        if allowed_host:
            allowed_hosts.append(allowed_host)
    return bool(host) and host in allowed_hosts


async def resolve_egress_access(
    session: AsyncSession,
    tenant_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    execution_ref = payload.get("execution_ref") or {}
    if execution_ref.get("type") not in VALID_EXECUTION_TYPES:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    target = payload.get("target") or {}
    if target.get("type") not in VALID_TARGET_TYPES:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)

    platform_key = str(payload.get("platform_key") or "")
    platform = (
        await session.execute(
            select(ProjectPlatform).where(
                ProjectPlatform.tenant_id == tenant_id,
                ProjectPlatform.key == platform_key,
                ProjectPlatform.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if platform is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "ProjectPlatform"})
    if not platform.enabled:
        raise AppError(ErrorCode.FORBIDDEN)

    if not _target_allowed(platform, target):
        raise AppError(ErrorCode.FORBIDDEN)

    actor_user_id = uuid.UUID(str(payload["actor_user_id"]))
    credential = await _resolve_credential(session, tenant_id, actor_user_id, platform)
    return {
        "decision": "ALLOW",
        "platform": _platform_snapshot(platform),
        "credential": credential,
    }
