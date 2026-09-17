from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class PlatformClient(Protocol):
    async def call(
        self,
        *,
        platform_key: str,
        target: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class SkillContext:
    actor_user_id: str
    run_id: str | None
    task_id: str | None
    platform: PlatformClient
