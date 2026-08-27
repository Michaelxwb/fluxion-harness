"""User Domain 三张新表的 SQL（Gate 1B / TASK-U102..U105）。

Profile 取 max(version) 为最新；Preference 单行 upsert；Grant 行级增删查。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import (
    capability_grants,
    user_preferences,
    user_profiles,
)


def _now() -> datetime:
    return datetime.now(UTC)


async def _latest_version(
    engine: AsyncEngine, tenant_id: str, platform_user_id: str
) -> int:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(user_profiles.c.version)
                .where(
                    user_profiles.c.tenant_id == tenant_id,
                    user_profiles.c.platform_user_id == platform_user_id,
                )
                .order_by(user_profiles.c.version.desc())
                .limit(1)
            )
        ).first()
    return int(row[0]) if row else 0


async def put_profile(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    platform_user_id: str,
    profile_json: dict[str, object],
) -> int:
    version = await _latest_version(engine, tenant_id, platform_user_id) + 1
    async with engine.begin() as conn:
        await conn.execute(
            insert(user_profiles).values(
                tenant_id=tenant_id,
                platform_user_id=platform_user_id,
                version=version,
                profile_json=profile_json,
                created_at=_now(),
            )
        )
    return version


async def get_latest_profile(
    engine: AsyncEngine, *, tenant_id: str, platform_user_id: str
) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    user_profiles.c.version,
                    user_profiles.c.profile_json,
                    user_profiles.c.created_at,
                )
                .where(
                    user_profiles.c.tenant_id == tenant_id,
                    user_profiles.c.platform_user_id == platform_user_id,
                )
                .order_by(user_profiles.c.version.desc())
                .limit(1)
            )
        ).mappings().first()
    if row is None:
        return None
    return {
        "version": int(row["version"]),
        "profile_json": dict(row["profile_json"]),
        "created_at": row["created_at"],
    }


async def put_preferences(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    platform_user_id: str,
    preference_json: dict[str, object],
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            delete(user_preferences).where(
                user_preferences.c.tenant_id == tenant_id,
                user_preferences.c.platform_user_id == platform_user_id,
            )
        )
        await conn.execute(
            insert(user_preferences).values(
                tenant_id=tenant_id,
                platform_user_id=platform_user_id,
                preference_json=preference_json,
                updated_at=_now(),
            )
        )


async def get_preferences(
    engine: AsyncEngine, *, tenant_id: str, platform_user_id: str
) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    user_preferences.c.preference_json,
                    user_preferences.c.updated_at,
                )
                .where(
                    user_preferences.c.tenant_id == tenant_id,
                    user_preferences.c.platform_user_id == platform_user_id,
                )
            )
        ).mappings().first()
    if row is None:
        return None
    return {"preference_json": dict(row["preference_json"]), "updated_at": row["updated_at"]}


async def add_grant(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    platform_user_id: str,
    capability_ref: str,
    granted_scope: str,
    version_pin: str | None,
) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(
            insert(capability_grants).values(
                tenant_id=tenant_id,
                platform_user_id=platform_user_id,
                capability_ref=capability_ref,
                granted_scope=granted_scope,
                version_pin=version_pin,
                created_at=_now(),
            )
        )
        return int(result.inserted_primary_key[0])


async def list_grants(
    engine: AsyncEngine, *, tenant_id: str, platform_user_id: str
) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(capability_grants)
                .where(
                    capability_grants.c.tenant_id == tenant_id,
                    capability_grants.c.platform_user_id == platform_user_id,
                )
                .order_by(capability_grants.c.id.asc())
            )
        ).mappings().all()
    return [dict(row) for row in rows]


async def revoke_grant(
    engine: AsyncEngine, *, tenant_id: str, platform_user_id: str, capability_ref: str
) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(
            delete(capability_grants).where(
                capability_grants.c.tenant_id == tenant_id,
                capability_grants.c.platform_user_id == platform_user_id,
                capability_grants.c.capability_ref == capability_ref,
            )
        )
    return int(result.rowcount or 0)


async def list_channel_identities_for_user(
    engine: AsyncEngine, *, tenant_id: str, platform_user_id: str
) -> list[dict[str, Any]]:
    from fluxion.registry.schema import channel_identities

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    channel_identities.c.channel_type,
                    channel_identities.c.channel_user_id,
                    channel_identities.c.created_at,
                ).where(
                    channel_identities.c.tenant_id == tenant_id,
                    channel_identities.c.platform_user_id == platform_user_id,
                )
            )
        ).mappings().all()
    return [dict(row) for row in rows]
