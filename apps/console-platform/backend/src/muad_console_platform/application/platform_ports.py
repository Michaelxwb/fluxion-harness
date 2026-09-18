from __future__ import annotations

import uuid
from typing import Any, Protocol


class PlatformSessionInvalidator(Protocol):
    async def clear_platform(self, *, tenant_id: str, platform_id: uuid.UUID) -> int: ...


class NullPlatformSessionInvalidator:
    async def clear_platform(self, *, tenant_id: str, platform_id: uuid.UUID) -> int:
        del tenant_id, platform_id
        return 0


def platform_snapshot(
    *,
    platform_id: uuid.UUID,
    key: str,
    name: str,
    resolver_type: str,
    resolver_config: dict[str, Any],
    adapter_key: str,
    adapter_config: dict[str, Any],
    adapter_schema_version: str,
    credential_mode: str,
    enabled: bool,
    update_time: Any,
    configured_user_credential_count: int,
    has_shared_credential: bool,
    adapter_metadata: dict[str, Any] | None = None,
    user_credential_status: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "platform_id": str(platform_id),
        "key": key,
        "name": name,
        "resolver_type": resolver_type,
        "resolver_config": resolver_config,
        "adapter_key": adapter_key,
        "adapter_config": adapter_config,
        "adapter_schema_version": adapter_schema_version,
        "credential_mode": credential_mode,
        "enabled": enabled,
        "configured_user_credential_count": configured_user_credential_count,
        "has_shared_credential": has_shared_credential,
        "update_time": update_time.isoformat() if hasattr(update_time, "isoformat") else str(update_time),
    }
    if adapter_metadata is not None:
        data["adapter_metadata"] = adapter_metadata
    if user_credential_status is not None:
        data["user_credential_status"] = user_credential_status
    return data
