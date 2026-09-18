from __future__ import annotations

from typing import Any

from muad_platform_sdk import GenericHttpAdapter, PlatformAdapter, PlatformAdapterRegistry


def build_default_registry() -> PlatformAdapterRegistry:
    registry = PlatformAdapterRegistry()
    registry.register(GenericHttpAdapter())
    return registry


def adapter_metadata(adapter: PlatformAdapter) -> dict[str, Any]:
    return {
        "key": adapter.key,
        "name": adapter.name,
        "version": adapter.version,
        "session_mode": str(adapter.session_mode),
        "platform_config_schema": adapter.platform_config_schema,
        "credential_schema": adapter.credential_schema,
    }
