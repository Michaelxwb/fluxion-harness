"""User Domain 领域服务（Gate 1B / TASK-U101..U105）。

纯组合层：typed 校验（ADR-011 SoT）+ store 门面调用；SQL 全部位于
registry/user_sqlalchemy。错误码集中在 errors.console（F9）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from fluxion.agents.capabilities import resolve_binding_reference
from fluxion.agents.definitions import CapabilityBinding
from fluxion.errors.console import (
    USER_NOT_BOUND,
    USER_NOT_FOUND,
    VALIDATION_FAILED,
    ConsoleError,
)
from fluxion.registry import ChannelIdentityRecord, ChannelRegistryStore, PlatformUserRecord
from fluxion.resources import ResourceKind, SubjectType
from fluxion.users.models import UserPreferenceSpec, UserProfileSpec


class UserDomainService:
    def __init__(self, store: ChannelRegistryStore) -> None:
        self._store = store

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    async def ensure_user(
        self, *, tenant_id: str, platform_user_id: str, display_name: str
    ) -> PlatformUserRecord:
        existing = await self._store.get_platform_user(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        if existing is not None:
            return existing
        return await self._store.create_platform_user(
            PlatformUserRecord(
                tenant_id=tenant_id,
                platform_user_id=platform_user_id,
                display_name=display_name,
                created_at=self._now(),
            )
        )

    async def list_users(
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[PlatformUserRecord], int]:
        return await self._store.list_platform_users(
            tenant_id=tenant_id, offset=offset, limit=limit
        )

    # ---- Profile（U102/U103）-------------------------------------------------

    async def upsert_profile(
        self, *, tenant_id: str, platform_user_id: str, spec: dict[str, object]
    ) -> dict[str, object]:
        try:
            validated = UserProfileSpec.model_validate(spec)
        except ValidationError as exc:
            raise ConsoleError(VALIDATION_FAILED, str(exc), 400) from exc
        await self._ensure_exists(tenant_id, platform_user_id)
        await self._store.put_user_profile(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            profile_json=validated.model_dump(mode="json"),
        )
        record = await self.get_profile(tenant_id=tenant_id, platform_user_id=platform_user_id)
        assert record is not None
        return record

    async def get_profile(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, object] | None:
        result: dict[str, object] | None = await self._store.get_latest_user_profile(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        return result

    # ---- Preferences（U104）--------------------------------------------------

    async def set_preferences(
        self, *, tenant_id: str, platform_user_id: str, spec: dict[str, object]
    ) -> dict[str, object]:
        try:
            validated = UserPreferenceSpec.model_validate(spec)
        except ValidationError as exc:
            raise ConsoleError(VALIDATION_FAILED, str(exc), 400) from exc
        await self._ensure_exists(tenant_id, platform_user_id)
        result: dict[str, object] = await self._store.put_user_preferences(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            preference_json=validated.model_dump(mode="json"),
        )
        return result

    async def get_preferences(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, object] | None:
        result: dict[str, object] | None = await self._store.get_user_preferences(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        return result

    # ---- Capability Grants（U105）--------------------------------------------

    async def grant(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        capability_binding: CapabilityBinding,
        granted_scope: str = "invoke",
    ) -> dict[str, object]:
        target = resolve_binding_reference(capability_binding)
        if target.resource_kind not in (ResourceKind.SKILL, ResourceKind.MCP):
            # TOOL 准入属 Agent allowlist 域；用户级授权只覆盖可被用户上下文
            # 授予的资源型能力（skill/mcp），与执行侧消费一致。
            raise ConsoleError(
                VALIDATION_FAILED,
                f"grant scope invalid for tool-capability: {target}",
                400,
            )
        await self._ensure_exists(tenant_id, platform_user_id)
        record = await self._store.add_capability_grant(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=target.resource_id,
            granted_scope=granted_scope,
            version_pin=target.version,
        )
        return {
            "id": record.id,
            "capability_ref": record.capability_ref,
            "resource_kind": target.resource_kind.value,
            "granted_scope": record.granted_scope,
            "version_pin": record.version_pin,
        }

    async def list_grants(self, *, tenant_id: str, platform_user_id: str) -> list[dict[str, object]]:
        records = await self._store.list_capability_grants(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        return [
            {
                "id": r.id,
                "capability_ref": r.capability_ref,
                "granted_scope": r.granted_scope,
                "version_pin": r.version_pin,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]

    async def revoke_grant(
        self, *, tenant_id: str, platform_user_id: str, capability_ref: str
    ) -> int:
        revoked: int = await self._store.revoke_capability_grant(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=capability_ref,
        )
        return revoked

    # ---- User 360（BE-S-08/S-10）----------------------------------------------

    async def user_360(self, *, tenant_id: str, platform_user_id: str) -> dict[str, object]:
        identity = await self._store.get_platform_user(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        if identity is None:
            raise ConsoleError(USER_NOT_FOUND, f"user_not_found: {platform_user_id}", 404)
        channels = await self._store.list_channel_identities_for_user(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        profile = await self.get_profile(tenant_id=tenant_id, platform_user_id=platform_user_id)
        preferences = await self.get_preferences(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        grants = await self.list_grants(tenant_id=tenant_id, platform_user_id=platform_user_id)
        policy_bindings = await self._store.list_bindings(
            tenant_id=tenant_id,
            subject_type=SubjectType.USER.value,
            subject_id=platform_user_id,
            resource_type=ResourceKind.POLICY,
        )
        audit_rows, _total = await self._store.list_audit(
            tenant_id=tenant_id, offset=0, limit=50
        )
        activity = [a for a in audit_rows if a.target_id == platform_user_id][:20]
        return {
            "identity": {
                "platform_user_id": identity.platform_user_id,
                "display_name": identity.display_name,
                "created_at": identity.created_at.isoformat(),
                "channels": channels,
            },
            "profile": profile["profile_json"] if profile else None,
            "profile_version": profile["version"] if profile else None,
            "preferences": preferences["preference_json"] if preferences else None,
            "capabilities": grants,
            "policy": [
                {"binding_id": b.binding_id, "resource_id": b.resource_id}
                for b in policy_bindings
            ],
            "activity_count": len(activity),
        }

    async def resolve_channel_identity_or_raise(
        self, *, tenant_id: str, channel_type: str, channel_user_id: str
    ) -> ChannelIdentityRecord:
        identity = await self._store.resolve_channel_identity(
            tenant_id=tenant_id, channel_type=channel_type, channel_user_id=channel_user_id
        )
        if identity is None:
            raise ConsoleError(
                USER_NOT_BOUND,
                f"user_not_bound: no platform_user for {channel_type}:{channel_user_id}",
                404,
            )
        return identity

    async def _ensure_exists(self, tenant_id: str, platform_user_id: str) -> None:
        existing = await self._store.get_platform_user(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        if existing is None:
            raise ConsoleError(USER_NOT_FOUND, f"user_not_found: {platform_user_id}", 404)
