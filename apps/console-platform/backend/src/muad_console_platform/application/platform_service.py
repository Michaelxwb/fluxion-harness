from __future__ import annotations

import uuid
from typing import Any

import jsonschema
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_platform_sdk import PlatformAdapterNotFound, PlatformAdapterRegistry
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ProjectPlatform
from ..infrastructure.repositories.credential_repository import CredentialRepository
from ..infrastructure.repositories.platform_repository import PlatformListRow, PlatformRepository
from .audit_service import AuditActor, AuditService
from .dto import PlatformCreateRequest, PlatformUpdateRequest
from .platform_adapter_service import adapter_metadata
from .platform_ports import (
    NullPlatformSessionInvalidator,
    PlatformSessionInvalidator,
    platform_config_from,
    platform_snapshot,
)

_RESOLVER_REQUIRED_FIELDS: dict[str, str] = {
    "BASE_URL": "base_url",
    "SERVICE_DISCOVERY": "service_name",
}


class PlatformService:
    def __init__(
        self,
        session: AsyncSession,
        registry: PlatformAdapterRegistry,
        sessions: PlatformSessionInvalidator | None = None,
    ) -> None:
        self._session = session
        self._registry = registry
        self._sessions = sessions or NullPlatformSessionInvalidator()
        self._platforms = PlatformRepository(session)
        self._credentials = CredentialRepository(session)
        self._audit = AuditService(session)

    def _validate(
        self,
        *,
        adapter_key: str,
        adapter_config: dict[str, Any],
        resolver_type: str,
        resolver_config: dict[str, Any],
    ) -> None:
        required_field = _RESOLVER_REQUIRED_FIELDS.get(resolver_type)
        if required_field is None:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        value = resolver_config.get(required_field)
        if not isinstance(value, str) or not value.strip():
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        try:
            adapter = self._registry.get(adapter_key)
        except PlatformAdapterNotFound as exc:
            raise AppError(ErrorCode.PLATFORM_ADAPTER_NOT_FOUND) from exc
        try:
            jsonschema.validate(adapter_config, adapter.platform_config_schema)
        except jsonschema.ValidationError as exc:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc

    async def list_platforms(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None,
        adapter_key: str | None,
        enabled: bool | None,
        user_id: uuid.UUID | None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = await self._platforms.list(
            tenant_id, page, page_size, keyword, adapter_key, enabled, user_id
        )
        return [self._summary(row, user_id is not None) for row in rows], total

    async def get_detail(self, tenant_id: str, platform_id: uuid.UUID) -> dict[str, Any]:
        platform = await self._platforms.get(tenant_id, platform_id)
        if platform is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return platform_snapshot(
            platform_id=platform.id,
            key=platform.key,
            name=platform.name,
            resolver_type=platform.resolver_type,
            resolver_config=platform.resolver_config_json,
            adapter_key=platform.adapter_key,
            adapter_config=platform.adapter_config_json,
            adapter_schema_version=platform.adapter_schema_version,
            credential_mode=platform.credential_mode,
            enabled=platform.enabled,
            update_time=platform.update_time,
            configured_user_credential_count=await self._platforms.configured_user_credential_count(
                tenant_id, platform.id
            ),
            has_shared_credential=await self._platforms.has_shared_credential(tenant_id, platform.id),
            adapter_metadata=self._adapter_metadata(platform.adapter_key),
        )

    async def create(
        self,
        tenant_id: str,
        payload: PlatformCreateRequest,
        actor: AuditActor,
    ) -> ProjectPlatform:
        self._validate(
            adapter_key=payload.adapter_key,
            adapter_config=payload.adapter_config,
            resolver_type=payload.resolver_type,
            resolver_config=payload.resolver_config,
        )
        if await self._platforms.find_by_key(tenant_id, payload.key) is not None:
            raise AppError(ErrorCode.COMMON_CONFLICT, message_args={"key": payload.key})
        platform = ProjectPlatform(
            tenant_id=tenant_id,
            key=payload.key,
            name=payload.name,
            resolver_type=payload.resolver_type,
            resolver_config_json=payload.resolver_config,
            adapter_key=payload.adapter_key,
            adapter_config_json=payload.adapter_config,
            adapter_schema_version=str(self._registry.get(payload.adapter_key).version),
            credential_mode=payload.credential_mode,
            enabled=payload.enabled,
        )
        try:
            await self._platforms.add(platform)
        except IntegrityError as exc:
            raise AppError(ErrorCode.COMMON_CONFLICT, message_args={"key": payload.key}) from exc
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type="project_platform",
            resource_id=platform.id,
            action="CREATE",
            before=None,
            after=self._snapshot(platform),
        )
        return platform

    async def update(
        self,
        tenant_id: str,
        platform_id: uuid.UUID,
        payload: PlatformUpdateRequest,
        actor: AuditActor,
    ) -> tuple[ProjectPlatform, bool]:
        platform = await self._platforms.get(tenant_id, platform_id)
        if platform is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        before = self._snapshot(platform)
        name = payload.name if payload.name is not None else platform.name
        resolver_type = (
            payload.resolver_type if payload.resolver_type is not None else platform.resolver_type
        )
        resolver_config = (
            payload.resolver_config
            if payload.resolver_config is not None
            else platform.resolver_config_json
        )
        adapter_key = (
            payload.adapter_key if payload.adapter_key is not None else platform.adapter_key
        )
        adapter_config = (
            payload.adapter_config
            if payload.adapter_config is not None
            else platform.adapter_config_json
        )
        credential_mode = (
            payload.credential_mode
            if payload.credential_mode is not None
            else platform.credential_mode
        )
        enabled = payload.enabled if payload.enabled is not None else platform.enabled
        adapter_changed = adapter_key != platform.adapter_key
        if (
            adapter_changed
            or adapter_config != platform.adapter_config_json
            or resolver_type != platform.resolver_type
            or resolver_config != platform.resolver_config_json
        ):
            self._validate(
                adapter_key=adapter_key,
                adapter_config=adapter_config,
                resolver_type=resolver_type,
                resolver_config=resolver_config,
            )
        platform.name = name
        platform.resolver_type = resolver_type
        platform.resolver_config_json = resolver_config
        platform.adapter_key = adapter_key
        platform.adapter_config_json = adapter_config
        platform.credential_mode = credential_mode
        platform.enabled = enabled
        if adapter_changed:
            platform.adapter_schema_version = str(self._registry.get(adapter_key).version)
            await self._credentials.invalidate_platform(tenant_id, platform.id)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type="project_platform",
            resource_id=platform.id,
            action="UPDATE",
            before=before,
            after=self._snapshot(platform),
        )
        if adapter_changed:
            await self._sessions.clear_platform(platform_config_from(platform))
        return platform, adapter_changed

    async def delete(self, tenant_id: str, platform_id: uuid.UUID, actor: AuditActor) -> None:
        platform = await self._platforms.get(tenant_id, platform_id)
        if platform is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        before = self._snapshot(platform)
        await self._credentials.invalidate_platform(tenant_id, platform.id)
        await self._platforms.soft_delete(platform)
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type="project_platform",
            resource_id=platform.id,
            action="DELETE",
            before=before,
            after=None,
        )
        await self._sessions.clear_platform(platform_config_from(platform))

    def _summary(self, row: PlatformListRow, with_status: bool) -> dict[str, Any]:
        platform = row.platform
        return platform_snapshot(
            platform_id=platform.id,
            key=platform.key,
            name=platform.name,
            resolver_type=platform.resolver_type,
            resolver_config=platform.resolver_config_json,
            adapter_key=platform.adapter_key,
            adapter_config=platform.adapter_config_json,
            adapter_schema_version=platform.adapter_schema_version,
            credential_mode=platform.credential_mode,
            enabled=platform.enabled,
            update_time=platform.update_time,
            configured_user_credential_count=row.configured_user_credential_count,
            has_shared_credential=row.has_shared_credential,
            user_credential_status=(row.user_credential_status or "NONE") if with_status else None,
        )

    def _snapshot(self, platform: ProjectPlatform) -> dict[str, Any]:
        return platform_snapshot(
            platform_id=platform.id,
            key=platform.key,
            name=platform.name,
            resolver_type=platform.resolver_type,
            resolver_config=platform.resolver_config_json,
            adapter_key=platform.adapter_key,
            adapter_config=platform.adapter_config_json,
            adapter_schema_version=platform.adapter_schema_version,
            credential_mode=platform.credential_mode,
            enabled=platform.enabled,
            update_time=platform.update_time,
            configured_user_credential_count=0,
            has_shared_credential=False,
        )

    def _adapter_metadata(self, adapter_key: str) -> dict[str, Any] | None:
        try:
            return adapter_metadata(self._registry.get(adapter_key))
        except PlatformAdapterNotFound:
            return None
