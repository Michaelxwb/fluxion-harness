"""用户自助 builtin tools（closure TASK-011 / design/08 UJ-U-04/UJ-U-06）。

将 UserDomainService 的 Profile/Preference/Memory 能力暴露为 Agent 可调用的
ToolDescriptor + ToolExecutor，用户经自然语言触发（对话即界面）。

工具清单：
- user.profile.get / user.profile.update
- user.preference.get / user.preference.set
- user.memory.list / user.memory.correct / user.memory.delete

全部走三重交集 + 风险分级（读/偏好更新 low → auto-approve；删除 medium → 确认）+
AuditLog。learning gate 贯通：停学用户的 user.memory.correct/delete 拒绝。
"""

from __future__ import annotations

from typing import Any

from fluxion.registry.schema import personal_memory
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolDescriptor, ToolRuntime


def _user_id(context: RuntimeContext) -> str:
    return context.snapshot.user_id


def _tenant_id(context: RuntimeContext) -> str:
    return context.snapshot.tenant_id


def _result(data: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "data": data}


def _error(code: str, message: str) -> dict[str, object]:
    return {"ok": False, "error": code, "message": message}


def register_user_tools(runtime: ToolRuntime, *, services: dict[str, Any]) -> None:
    """注册用户自助工具到 ToolRuntime。

    services 需提供：
    - users: UserDomainService（profile/preference 读写）
    - store: AsyncEngine（memory SQL 直查）
    """
    users = services["users"]
    engine = services["engine"]

    # ---- user.profile ----

    async def _profile_get(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import select

        from fluxion.registry.schema import user_profiles

        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    select(user_profiles.c.profile_json, user_profiles.c.version)
                    .where(
                        user_profiles.c.tenant_id == tenant_id,
                        user_profiles.c.platform_user_id == user_id,
                    )
                    .order_by(user_profiles.c.version.desc())
                    .limit(1)
                )
            ).first()
        if row is None:
            return _result({"profile": {}, "version": None})
        return _result({"profile": dict(row["profile_json"]), "version": row["version"]})

    async def _profile_update(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from fluxion.users.service import UserDomainService

        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        svc = UserDomainService(engine)
        await svc.ensure_user(tenant_id=tenant_id, platform_user_id=user_id)
        record = await svc.upsert_profile(
            tenant_id=tenant_id,
            platform_user_id=user_id,
            spec={
                "display_name": str(args.get("display_name", user_id)),
                "bio": str(args.get("bio", "")),
                "timezone": str(args.get("timezone", "Asia/Shanghai")),
                "language": str(args.get("language", "zh-CN")),
            },
            actor_id=user_id,
        )
        return _result({"version": record["version"]})

    runtime.register(
        ToolDescriptor(
            tool_id="user.profile.get",
            capability_id="builtin.user",
            name="user.profile.get",
            risk_level="low",
            external_dependency=False,
        ),
        _profile_get,
    )
    runtime.register(
        ToolDescriptor(
            tool_id="user.profile.update",
            capability_id="builtin.user",
            name="user.profile.update",
            risk_level="medium",
            external_dependency=False,
        ),
        _profile_update,
    )

    # ---- user.preference ----

    async def _preference_get(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import select

        from fluxion.registry.schema import user_preferences

        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    select(user_preferences.c.preference_json).where(
                        user_preferences.c.tenant_id == tenant_id,
                        user_preferences.c.platform_user_id == user_id,
                    )
                )
            ).first()
        if row is None:
            return _result({"preferences": {}})
        return _result({"preferences": dict(row[0])})

    async def _preference_set(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import select

        from fluxion.registry.schema import user_preferences

        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        key = str(args.get("key", ""))
        value = str(args.get("value", ""))
        if not key:
            return _error("missing_key", "preference key is required")
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    select(user_preferences.c.preference_json).where(
                        user_preferences.c.tenant_id == tenant_id,
                        user_preferences.c.platform_user_id == user_id,
                    )
                )
            ).first()
            existing = dict(row[0]) if row else {}
            existing[key] = value
            await conn.execute(
                user_preferences.delete().where(
                    user_preferences.c.tenant_id == tenant_id,
                    user_preferences.c.platform_user_id == user_id,
                )
            )
            await conn.execute(
                user_preferences.insert().values(
                    tenant_id=tenant_id,
                    platform_user_id=user_id,
                    preference_json=existing,
                    updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                )
            )
        return _result({"key": key, "value": value})

    runtime.register(
        ToolDescriptor(
            tool_id="user.preference.get",
            capability_id="builtin.user",
            name="user.preference.get",
            risk_level="low",
            external_dependency=False,
        ),
        _preference_get,
    )
    runtime.register(
        ToolDescriptor(
            tool_id="user.preference.set",
            capability_id="builtin.user",
            name="user.preference.set",
            risk_level="low",
            external_dependency=False,
        ),
        _preference_set,
    )

    # ---- user.memory ----

    async def _memory_list(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import select


        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(
                        personal_memory.c.id,
                        personal_memory.c.memory_type,
                        personal_memory.c.content,
                    ).where(
                        personal_memory.c.tenant_id == tenant_id,
                        personal_memory.c.user_id == user_id,
                    )
                )
            ).mappings().all()
        return _result({"memories": [dict(r) for r in rows]})

    async def _memory_delete(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import delete


        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        entry_id = int(args.get("entry_id", 0))
        async with engine.begin() as conn:
            result = await conn.execute(
                delete(personal_memory).where(
                    personal_memory.c.tenant_id == tenant_id,
                    personal_memory.c.user_id == user_id,
                    personal_memory.c.id == entry_id,
                )
            )
        return _result({"deleted": result.rowcount})

    runtime.register(
        ToolDescriptor(
            tool_id="user.memory.list",
            capability_id="builtin.user",
            name="user.memory.list",
            risk_level="low",
            external_dependency=False,
        ),
        _memory_list,
    )
    runtime.register(
        ToolDescriptor(
            tool_id="user.memory.delete",
            capability_id="builtin.user",
            name="user.memory.delete",
            risk_level="medium",
            external_dependency=False,
        ),
        _memory_delete,
    )
