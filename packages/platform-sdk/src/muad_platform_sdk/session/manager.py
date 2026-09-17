from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..types import PlatformConfig, PlatformSession, SecretValue


@dataclass(frozen=True, slots=True)
class SessionRequest:
    platform: PlatformConfig
    actor_scope: str
    credential_version: str
    credential_ref: str | None
    credential: SecretValue | None


class PlatformSessionManager(Protocol):
    async def acquire(self, request: SessionRequest) -> PlatformSession | None: ...

    async def invalidate(self, *, platform: PlatformConfig, actor_scope: str) -> None: ...

    async def renew(self, request: SessionRequest) -> PlatformSession | None: ...
