from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import jsonschema
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_platform_sdk import PlatformAdapterNotFound, PlatformAdapterRegistry
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.credential_repository import CredentialRepository
from ..infrastructure.repositories.platform_repository import PlatformRepository


class PlatformTestService:
    def __init__(self, session: AsyncSession, registry: PlatformAdapterRegistry) -> None:
        self._session = session
        self._registry = registry
        self._platforms = PlatformRepository(session)
        self._credentials = CredentialRepository(session)

    async def test_platform(
        self,
        tenant_id: str,
        platform_id: uuid.UUID,
        *,
        test_user_id: uuid.UUID | None,
        timeout_ms: int,
    ) -> dict[str, Any]:
        platform = await self._platforms.get(tenant_id, platform_id)
        if platform is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        try:
            adapter = self._registry.get(platform.adapter_key)
        except PlatformAdapterNotFound as exc:
            raise AppError(ErrorCode.PLATFORM_ADAPTER_NOT_FOUND) from exc
        try:
            jsonschema.validate(platform.adapter_config_json, adapter.platform_config_schema)
        except jsonschema.ValidationError as exc:
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc

        connectivity, details = await self._probe(
            resolver_type=platform.resolver_type,
            resolver_config=platform.resolver_config_json,
            timeout_ms=timeout_ms,
        )
        credential_ref_status = await self._credential_ref_status(
            tenant_id=tenant_id,
            platform_id=platform_id,
            credential_mode=platform.credential_mode,
            test_user_id=test_user_id,
        )
        return {
            "config_valid": True,
            "adapter_key": platform.adapter_key,
            "adapter_version": str(adapter.version),
            "resolver_type": platform.resolver_type,
            "connectivity": connectivity,
            "credential_ref_status": credential_ref_status,
            "checked_at": datetime.now(UTC).isoformat(),
            "details": details,
        }

    async def _credential_ref_status(
        self,
        *,
        tenant_id: str,
        platform_id: uuid.UUID,
        credential_mode: str,
        test_user_id: uuid.UUID | None,
    ) -> str:
        if credential_mode == "NONE":
            return "NOT_CHECKED"
        if credential_mode == "SHARED_ONLY":
            await self._require_shared_credential(tenant_id, platform_id)
            return "ACTIVE"
        if test_user_id is None:
            return "NOT_CHECKED"
        if credential_mode in ("USER_ONLY", "USER_THEN_SHARED"):
            user_status = await self._credentials.get_user_status(tenant_id, test_user_id, platform_id)
            if user_status == "ACTIVE":
                return "ACTIVE"
            if credential_mode == "USER_ONLY":
                raise AppError(ErrorCode.CREDENTIAL_MISSING)
        await self._require_shared_credential(tenant_id, platform_id)
        return "ACTIVE"

    async def _require_shared_credential(self, tenant_id: str, platform_id: uuid.UUID) -> None:
        if await self._credentials.get_shared_status(tenant_id, platform_id) != "ACTIVE":
            raise AppError(ErrorCode.CREDENTIAL_MISSING)

    async def _probe(
        self,
        *,
        resolver_type: str,
        resolver_config: dict[str, Any],
        timeout_ms: int,
    ) -> tuple[str, dict[str, Any]]:
        timeout = max(timeout_ms, 1) / 1000
        if resolver_type == "BASE_URL":
            raw = str(resolver_config.get("base_url") or "")
            parsed = urlparse(raw)
            if parsed.hostname is None:
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            started = time.monotonic()
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(parsed.hostname, port), timeout=timeout
                )
                writer.close()
                await writer.wait_closed()
                del reader
            except (TimeoutError, OSError) as exc:
                return "UNREACHABLE", {
                    "host": parsed.hostname,
                    "port": port,
                    "error": type(exc).__name__,
                }
            return "REACHABLE", {
                "host": parsed.hostname,
                "port": port,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        if resolver_type == "SERVICE_DISCOVERY":
            service_name = str(resolver_config.get("service_name") or "")
            if not service_name:
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.getaddrinfo(service_name, None),
                    timeout=timeout,
                )
            except (TimeoutError, OSError) as exc:
                return "UNREACHABLE", {
                    "service_name": service_name,
                    "error": type(exc).__name__,
                }
            return "REACHABLE", {"service_name": service_name}
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
