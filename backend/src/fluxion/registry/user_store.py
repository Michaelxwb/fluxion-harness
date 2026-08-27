"""User Domain 记录类型（Gate 1B）。

与 channel_store 同层惯例：跨 registry 门面与领域服务共享的持久化记录。
spec 载荷的 typed 校验模型在 fluxion/users/models.py（ADR-011 SoT）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CapabilityGrantRecord:
    """用户级能力授权（U105）。capability_ref 经 agents.capabilities 归一。"""

    id: int
    tenant_id: str
    platform_user_id: str
    capability_ref: str
    granted_scope: str
    version_pin: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PreferenceRecord:
    tenant_id: str
    platform_user_id: str
    preference_json: dict[str, object]
    updated_at: datetime


class UserDomainStore(Protocol):
    """User Domain 三张新表的门面契约（TASK-007）。"""

    async def put_user_profile(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        profile_json: dict[str, object],
    ) -> int: ...

    async def get_latest_user_profile(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, object] | None: ...

    async def put_user_preferences(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        preference_json: dict[str, object],
    ) -> dict[str, object]: ...

    async def get_user_preferences(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, object] | None: ...

    async def add_capability_grant(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        capability_ref: str,
        granted_scope: str,
        version_pin: str | None,
    ) -> CapabilityGrantRecord: ...

    async def list_capability_grants(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[CapabilityGrantRecord]: ...

    async def revoke_capability_grant(
        self, *, tenant_id: str, platform_user_id: str, capability_ref: str
    ) -> int: ...

    async def list_channel_identities_for_user(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[dict[str, object]]: ...
