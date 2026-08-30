"""User Domain 领域服务（Gate 1B / TASK-U101..U105）。

纯组合层：typed 校验（ADR-011 SoT）+ store 门面调用；SQL 全部位于
registry/user_sqlalchemy。错误码集中在 errors.console（F9）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic import ValidationError

from fluxion.agents.capabilities import resolve_binding_reference
from fluxion.agents.definitions import AgentCapabilityReference
from fluxion.errors.console import (
    USER_NOT_BOUND,
    USER_NOT_FOUND,
    VALIDATION_FAILED,
    ConsoleError,
)
from fluxion.registry import (
    AuditRecord,
    ChannelIdentityRecord,
    ChannelRegistryStore,
    PlatformUserRecord,
    ProfileAttributeRecord,
)
from fluxion.resources import ResourceKind, SubjectType
from fluxion.users.models import ProfileAttribute, UserPreferenceSpec, UserProfileSpec


class UserDomainService:
    def __init__(self, store: ChannelRegistryStore) -> None:
        self._store = store

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    async def ensure_user(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        display_name: str,
        actor_id: str = "unknown",
        request_id: str = "",
    ) -> PlatformUserRecord:
        existing = await self._store.get_platform_user(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        if existing is None:
            created = await self._store.create_platform_user(
                PlatformUserRecord(
                    tenant_id=tenant_id,
                    platform_user_id=platform_user_id,
                    display_name=display_name,
                    created_at=self._now(),
                )
            )
            await self._audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                action="user.create",
                target_id=platform_user_id,
                after={"display_name": display_name},
            )
            return created
        return existing

    async def list_users(
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[PlatformUserRecord], int]:
        return await self._store.list_platform_users(
            tenant_id=tenant_id, offset=offset, limit=limit
        )

    # ---- Profile（U102/U103）-------------------------------------------------

    async def upsert_profile(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        spec: dict[str, object],
        actor_id: str = "unknown",
        request_id: str = "",
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
        audit_after: dict[str, object] = {"version": record["version"]}
        profile_json = cast(dict[str, object], record["profile_json"])
        audit_after.update(profile_json)
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request_id=request_id,
            action="user.profile.update",
            target_id=platform_user_id,
            after=audit_after,
        )
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
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        spec: dict[str, object],
        actor_id: str = "unknown",
        request_id: str = "",
    ) -> dict[str, object]:
        try:
            validated = UserPreferenceSpec.model_validate(spec)
        except ValidationError as exc:
            raise ConsoleError(VALIDATION_FAILED, str(exc), 400) from exc
        await self._ensure_exists(tenant_id, platform_user_id)
        payload_json = validated.model_dump(mode="json")
        record: dict[str, object] = await self._store.put_user_preferences(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            preference_json=payload_json,
        )
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request_id=request_id,
            action="user.preference.update",
            target_id=platform_user_id,
            after=payload_json,
        )
        return record

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
        capability_binding: AgentCapabilityReference,
        granted_scope: str = "invoke",
        actor_id: str = "unknown",
        request_id: str = "",
    ) -> dict[str, object]:
        if granted_scope not in ("invoke", "manage"):
            raise ConsoleError(VALIDATION_FAILED, f"granted_scope 无效: {granted_scope}", 400)
        target = resolve_binding_reference(capability_binding)
        # closure TASK-013（ADR-A002/ARCH-06）：Tool 用户授权维度恢复——
        # grant 支持 skill/tool/mcp；kind 落 capability_grants.capability_kind。
        await self._ensure_exists(tenant_id, platform_user_id)
        record = await self._store.add_capability_grant(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=target.resource_id,
            capability_kind=target.resource_kind.value,
            granted_scope=granted_scope,
            version_pin=target.version,
        )
        result = {
            "id": record.id,
            "capability_ref": record.capability_ref,
            "resource_kind": target.resource_kind.value,
            "granted_scope": record.granted_scope,
            "version_pin": record.version_pin,
        }
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request_id=request_id,
            action="user.capability.grant",
            target_id=platform_user_id,
            after=result,
        )
        return result

    async def list_grants(self, *, tenant_id: str, platform_user_id: str) -> list[dict[str, object]]:
        records = await self._store.list_capability_grants(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        return [
            {
                "id": r.id,
                "capability_ref": r.capability_ref,
                "resource_kind": r.capability_kind,
                "granted_scope": r.granted_scope,
                "version_pin": r.version_pin,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]

    async def revoke_grant(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        capability_ref: str,
        actor_id: str = "unknown",
        request_id: str = "",
    ) -> int:
        revoked: int = await self._store.revoke_capability_grant(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=capability_ref,
        )
        if revoked:
            await self._audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                action="user.capability.revoke",
                target_id=platform_user_id,
                after={"capability_ref": capability_ref},
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

    async def _audit(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        request_id: str,
        action: str,
        target_id: str,
        after: dict[str, object],
    ) -> None:
        # 规则 24：变更类高影响操作进独立 AuditLog（非普通日志）；载荷仅含
        # 结构化业务字段，不携带凭据/明文。
        await self._store.append_audit(
            AuditRecord(
                audit_id=f"audit_{uuid4().hex}",
                tenant_id=tenant_id,
                actor_id=actor_id or "unknown",
                request_id=request_id,
                action=action,
                target_type="platform_user",
                target_id=target_id,
                before=None,
                after=after,
            )
        )

    # ---- ProfileAttribute（P1C-09 / closure TASK-004）------------------------

    async def upsert_profile_attribute(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        key: str,
        value: str,
        source: str,
        source_ref: str | None,
        confidence: float,
        is_explicit: bool,
        actor_id: str = "unknown",
        request_id: str = "",
        user_editable: bool = True,
        visibility: str = "private",
    ) -> ProfileAttributeRecord:
        attribute = ProfileAttribute(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            key=key,
            value=value,
            source=source,  # type: ignore[arg-type]
            source_ref=source_ref,
            confidence=confidence,
            is_explicit=is_explicit,
            user_editable=user_editable,
            visibility=visibility,  # type: ignore[arg-type]
        )
        row = await self._store.upsert_profile_attribute(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            attribute=attribute.model_dump(include=_ATTRIBUTE_FIELDS, exclude_none=True),
        )
        record = ProfileAttributeRecord(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            key=key,
            value=str(row["value"]),
            source=str(row["source"]),
            source_ref=row.get("source_ref"),
            confidence=float(row["confidence"]),
            is_explicit=bool(row["is_explicit"]),
            user_editable=bool(row["user_editable"]),
            visibility=str(row["visibility"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        await self._audit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request_id=request_id,
            action="user.profile_attribute.upsert",
            target_id=platform_user_id,
            after={"key": key, "value": value, "source": source},
        )
        return record

    async def list_profile_attributes(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[ProfileAttributeRecord]:
        rows = await self._store.list_profile_attributes(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        return [
            ProfileAttributeRecord(
                tenant_id=row["tenant_id"],
                platform_user_id=row["platform_user_id"],
                key=row["key"],
                value=row["value"],
                source=row["source"],
                source_ref=row.get("source_ref"),
                confidence=float(row["confidence"]),
                is_explicit=bool(row["is_explicit"]),
                user_editable=bool(row["user_editable"]),
                visibility=row["visibility"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def delete_profile_attribute(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        key: str,
        actor_id: str = "unknown",
        request_id: str = "",
    ) -> int:
        deleted: int = await self._store.delete_profile_attribute(
            tenant_id=tenant_id, platform_user_id=platform_user_id, key=key
        )
        if deleted:
            await self._audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                action="user.profile_attribute.delete",
                target_id=platform_user_id,
                after={"key": key},
            )
        return deleted

    async def write_learned_attribute(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        key: str,
        value: str,
        source_ref: str | None = None,
        confidence: float = 0.9,
        actor_id: str = "learner",
        request_id: str = "",
    ) -> ProfileAttributeRecord:
        """learned 写入的唯一入口：learning_enabled=False 时拒绝（停学 gate）。"""
        prefs = await self._store.get_user_preferences(
            tenant_id=tenant_id, platform_user_id=platform_user_id
        )
        learning_enabled = True
        if prefs is not None:
            payload = cast(dict[str, object], prefs["preference_json"])
            learning_enabled = bool(payload.get("learning_enabled", True))
        if not learning_enabled:
            raise ConsoleError(
                VALIDATION_FAILED,
                f"learning_disabled: user {platform_user_id} 已关闭自动学习",
                422,
            )
        return await self.upsert_profile_attribute(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            key=key,
            value=value,
            source="conversation",
            source_ref=source_ref,
            confidence=confidence,
            is_explicit=False,
            actor_id=actor_id,
            request_id=request_id,
        )

_ATTRIBUTE_FIELDS = {'value', 'source', 'source_ref', 'confidence', 'is_explicit', 'user_editable', 'visibility', 'valid_from', 'valid_until', 'superseded_by', 'key'}
