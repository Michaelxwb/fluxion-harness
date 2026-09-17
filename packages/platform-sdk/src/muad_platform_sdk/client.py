from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .types import PlatformRequest


class PlatformClient(Protocol):
    async def call(self, platform_key: str, request: PlatformRequest) -> Mapping[str, Any]: ...

    async def request(
        self,
        platform_key: str,
        service: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...
