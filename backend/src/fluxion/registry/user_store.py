"""User Domain 记录类型（Gate 1B）。

与 channel_store 同层惯例：跨 registry 门面与领域服务共享的持久化记录。
spec 载荷的 typed 校验模型在 fluxion/users/models.py（ADR-011 SoT）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CapabilityGrantRecord:
    """用户级能力授权（U105）。capability_ref 经 agents.capabilities 归一。"""

    id: int
    tenant_id: str
    platform_user_id: str
    capability_ref: str
    capability_kind: str
    granted_scope: str
    version_pin: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PreferenceRecord:
    tenant_id: str
    platform_user_id: str
    preference_json: dict[str, object]
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileAttributeRecord:
    """行级用户画像属性（P1C-09）：key 唯一，provenance 随行持久化。"""

    tenant_id: str
    platform_user_id: str
    key: str
    value: str
    source: str
    source_ref: str | None
    confidence: float
    is_explicit: bool
    user_editable: bool
    visibility: str
    created_at: datetime
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
        capability_kind: str = "skill",
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

    async def upsert_profile_attribute(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        attribute: dict[str, object],
    ) -> dict[str, Any]: ...

    async def list_profile_attributes(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[dict[str, Any]]: ...

    async def delete_profile_attribute(
        self, *, tenant_id: str, platform_user_id: str, key: str
    ) -> int: ...
