from __future__ import annotations

from typing import Any, Protocol


class PlatformAdapter(Protocol):
    key: str
    version: str

    async def authenticate(self, credential: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]: ...
    async def validate(self, session: dict[str, Any], config: dict[str, Any]) -> bool: ...
    async def prepare_request(
        self,
        session: dict[str, Any],
        target: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...
