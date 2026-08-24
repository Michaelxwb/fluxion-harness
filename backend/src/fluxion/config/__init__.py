from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DevModeSettings:
    enabled: bool = False
    tenant_id: str = "dev"
    actor_id: str = "admin"

    @classmethod
    def from_env(cls) -> DevModeSettings:
        return cls(enabled=os.environ.get("FLUXION_DEV_MODE") == "1")


__all__ = ["DevModeSettings"]
