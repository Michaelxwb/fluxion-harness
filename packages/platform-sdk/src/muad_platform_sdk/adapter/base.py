from __future__ import annotations

from typing import Any, Protocol

from ..types import (
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PreparedRequest,
    SecretValue,
    SessionMode,
)


class PlatformAdapter(Protocol):
    key: str
    name: str
    version: str
    session_mode: SessionMode

    platform_config_schema: dict[str, Any]
    credential_schema: dict[str, Any]

    async def authenticate(
        self,
        platform: PlatformConfig,
        credential: SecretValue,
    ) -> PlatformSession | None: ...

    async def validate(
        self,
        platform: PlatformConfig,
        session: PlatformSession,
    ) -> bool: ...

    async def prepare_request(
        self,
        platform: PlatformConfig,
        session: PlatformSession | None,
        request: PlatformRequest,
        credential: SecretValue | None,
    ) -> PreparedRequest: ...
