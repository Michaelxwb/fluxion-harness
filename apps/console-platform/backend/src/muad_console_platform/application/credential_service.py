from __future__ import annotations

import uuid
from typing import Any

import jsonschema
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_platform_sdk import PlatformAdapterNotFound, PlatformAdapterRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.credential_repository import CredentialRepository
from ..infrastructure.repositories.platform_repository import PlatformRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from .audit_service import AuditActor, AuditService


class CredentialService:
    def __init__(self, session: AsyncSession, registry: PlatformAdapterRegistry) -> None:
        self._session = session
        self._registry = registry
        self._platforms = PlatformRepository(session)
        self._users = PlatformUserRepository(session)
        self._credentials = CredentialRepository(session)
        self._audit = AuditService(session)

    async def _validate(self, tenant_id: str, platform_id: uuid.UUID, payload: dict[str, Any]) -> str:
        platform = await self._platforms.get(tenant_id, platform_id)
        if platform is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        try:
            adapter = self._registry.get(platform.adapter_key)
        except PlatformAdapterNotFound as exc:
            raise AppError(ErrorCode.PLATFORM_ADAPTER_NOT_FOUND) from exc
        try:
            jsonschema.validate(payload, adapter.credential_schema)
        except jsonschema.ValidationError as exc:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc
        return str(adapter.version)

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def get_user_credential(
        self, tenant_id: str, platform_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, Any]:
        await self._require_platform(tenant_id, platform_id)
        credential = await self._credentials.get_user(tenant_id, user_id, platform_id)
        if credential is None:
            return {
                "user_id": str(user_id),
                "platform_id": str(platform_id),
                "configured": False,
            }
        return {
            "user_id": str(user_id),
            "platform_id": str(platform_id),
            "configured": True,
            "credential_schema_version": credential.credential_schema_version,
            "status": credential.status,
            "last_verified_at": (
                credential.last_verified_at.isoformat() if credential.last_verified_at else None
            ),
        }

    async def save_user_credential(
        self,
        tenant_id: str,
        platform_id: uuid.UUID,
        user_id: uuid.UUID,
        payload: dict[str, Any],
        actor: AuditActor,
    ) -> dict[str, Any]:
        schema_version = await self._validate(tenant_id, platform_id, payload)
        await self._require_user(tenant_id, user_id)
        before = await self.get_user_credential(tenant_id, platform_id, user_id)
        credential = await self._credentials.upsert_user(
            tenant_id=tenant_id,
            user_id=user_id,
            platform_id=platform_id,
            credential_json=payload,
            schema_version=schema_version,
        )
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type="user_credential_ref",
            resource_id=credential.id,
            action="UPDATE" if before["configured"] else "CREATE",
            before=before if before["configured"] else None,
            after={
                "user_id": str(user_id),
                "platform_id": str(platform_id),
                "credential_schema_version": schema_version,
                "status": "ACTIVE",
            },
        )
        return {
            "user_id": str(user_id),
            "platform_id": str(platform_id),
            "credential_schema_version": schema_version,
            "status": "ACTIVE",
        }

    async def delete_user_credential(
        self, tenant_id: str, platform_id: uuid.UUID, user_id: uuid.UUID, actor: AuditActor
    ) -> None:
        await self._require_platform(tenant_id, platform_id)
        credential = await self._credentials.get_user(tenant_id, user_id, platform_id)
        if credential is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        await self._credentials.soft_delete_user(credential)
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type="user_credential_ref",
            resource_id=credential.id,
            action="DELETE",
            before={"user_id": str(user_id), "platform_id": str(platform_id)},
            after=None,
        )

    async def get_shared_credential(self, tenant_id: str, platform_id: uuid.UUID) -> dict[str, Any]:
        await self._require_platform(tenant_id, platform_id)
        credential = await self._credentials.get_shared(tenant_id, platform_id)
        if credential is None:
            return {"platform_id": str(platform_id), "configured": False}
        return {
            "platform_id": str(platform_id),
            "configured": True,
            "credential_schema_version": credential.credential_schema_version,
            "status": credential.status,
        }

    async def save_shared_credential(
        self,
        tenant_id: str,
        platform_id: uuid.UUID,
        payload: dict[str, Any],
        actor: AuditActor,
    ) -> dict[str, Any]:
        schema_version = await self._validate(tenant_id, platform_id, payload)
        before = await self.get_shared_credential(tenant_id, platform_id)
        credential = await self._credentials.upsert_shared(
            tenant_id=tenant_id,
            platform_id=platform_id,
            credential_json=payload,
            schema_version=schema_version,
        )
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type="shared_credential_ref",
            resource_id=credential.id,
            action="UPDATE" if before["configured"] else "CREATE",
            before=before if before["configured"] else None,
            after={
                "platform_id": str(platform_id),
                "credential_schema_version": schema_version,
                "status": "ACTIVE",
            },
        )
        return {
            "platform_id": str(platform_id),
            "configured": True,
            "credential_schema_version": schema_version,
            "status": "ACTIVE",
        }

    async def delete_shared_credential(
        self, tenant_id: str, platform_id: uuid.UUID, actor: AuditActor
    ) -> None:
        await self._require_platform(tenant_id, platform_id)
        credential = await self._credentials.get_shared(tenant_id, platform_id)
        if credential is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        await self._credentials.soft_delete_shared(credential)
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type="shared_credential_ref",
            resource_id=credential.id,
            action="DELETE",
            before={"platform_id": str(platform_id)},
            after=None,
        )

    async def _require_platform(self, tenant_id: str, platform_id: uuid.UUID) -> None:
        if await self._platforms.get(tenant_id, platform_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
