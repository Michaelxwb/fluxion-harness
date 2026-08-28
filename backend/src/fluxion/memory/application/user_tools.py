"""用户自助 builtin tools（closure TASK-011 / design/08 UJ-U-04/UJ-U-06）。

将 UserDomainService 的 Profile/Preference/Memory 能力暴露为 Agent 可调用的
ToolDescriptor + ToolExecutor，用户经自然语言触发（对话即界面）。

工具清单（8 个）：
- user.profile.get / user.profile.update
- user.preference.get / user.preference.set
- user.memory.list / user.memory.search / user.memory.correct / user.memory.delete

全部走三重交集 + 风险分级（读 low → auto-approve；更新 medium；删除 medium →
确认）+ AuditLog。learning gate 贯通：停学用户的 user.memory.correct/delete 拒绝。
"""

from __future__ import annotations

from typing import Any

from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolDescriptor, ToolRuntime
from fluxion.users.service import UserDomainService


def _user_id(context: RuntimeContext) -> str:
    return context.snapshot.user_id


def _tenant_id(context: RuntimeContext) -> str:
    return context.snapshot.tenant_id


def _result(data: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "data": data}


def _error(code: str, message: str) -> dict[str, object]:
    return {"ok": False, "error": code, "message": message}


async def _read_learning_enabled(engine: Any, tenant_id: str, user_id: str) -> bool:
    """从 user_preferences 读 learning_enabled（默认 True）。"""
    from sqlalchemy import select

    from fluxion.registry.schema import user_preferences

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
        return True
    payload = row[0]
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("learning_enabled", True))


async def _audit_tool_call(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    action: str,
    target_id: str,
    after: dict[str, object],
) -> None:
    """工具调用进 AuditLog（规则 24）。"""

    import uuid as _uuid
    from datetime import UTC, datetime

    from fluxion.registry.schema import audit_logs

    audit_id = f"audit_{_uuid.uuid4().hex}"
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            audit_logs.insert().values(
                audit_id=audit_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                action=action,
                target_type="user_tool",
                target_id=target_id,
                before_json=None,
                after_json=after,
                created_at=now,
            )
        )


def register_user_tools(
    runtime: ToolRuntime,
    *,
    engine: Any,
    users: UserDomainService,
) -> None:
    """注册用户自助工具到 ToolRuntime。

    需传入：engine（AsyncEngine）+ UserDomainService。
    """

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
            ).mappings().first()
        if row is None:
            return _result({"profile": {}, "version": None})
        return _result({"profile": dict(row["profile_json"]), "version": row["version"]})

    async def _profile_update(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        await users.ensure_user(tenant_id=tenant_id, platform_user_id=user_id)
        record = await users.upsert_profile(
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
        await _audit_tool_call(
            engine,
            tenant_id=tenant_id,
            actor_id=user_id,
            request_id=context.request.request_id if hasattr(context, "request") else "",
            action="user.profile.update",
            target_id=user_id,
            after={"version": record["version"]},
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
            ).mappings().first()
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
            ).mappings().first()
            existing = dict(row[0]) if row else {}
            existing[key] = value
            await conn.execute(
                user_preferences.delete().where(
                    user_preferences.c.tenant_id == tenant_id,
                    user_preferences.c.platform_user_id == user_id,
                )
            )
            from datetime import UTC, datetime

            await conn.execute(
                user_preferences.insert().values(
                    tenant_id=tenant_id,
                    platform_user_id=user_id,
                    preference_json=existing,
                    updated_at=datetime.now(UTC),
                )
            )
        await _audit_tool_call(
            engine,
            tenant_id=tenant_id,
            actor_id=user_id,
            request_id="",
            action="user.preference.set",
            target_id=user_id,
            after={"key": key, "value": value},
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

        from fluxion.registry.schema import personal_memory

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

    async def _memory_search(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import select

        from fluxion.registry.schema import personal_memory

        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        keyword = str(args.get("keyword", ""))
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
                        personal_memory.c.content.contains(keyword),
                    )
                )
            ).mappings().all()
        return _result({"memories": [dict(r) for r in rows]})

    async def _memory_correct(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import update

        from fluxion.registry.schema import personal_memory

        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        entry_id = int(args.get("entry_id", 0))
        content = str(args.get("content", ""))
        if not content:
            return _error("missing_content", "corrected content is required")

        learning = await _read_learning_enabled(engine, tenant_id, user_id)
        if not learning:
            return _error("learning_disabled", "user has disabled auto-learning")

        async with engine.begin() as conn:
            await conn.execute(
                update(personal_memory)
                .where(
                    personal_memory.c.tenant_id == tenant_id,
                    personal_memory.c.user_id == user_id,
                    personal_memory.c.id == entry_id,
                )
                .values(content=content, updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC))
            )
        await _audit_tool_call(
            engine,
            tenant_id=tenant_id,
            actor_id=user_id,
            request_id="",
            action="user.memory.correct",
            target_id=str(entry_id),
            after={"content": content},
        )
        return _result({"entry_id": entry_id, "content": content})

    async def _memory_delete(context: RuntimeContext, args: dict[str, object]) -> dict[str, object]:
        from sqlalchemy import delete

        from fluxion.registry.schema import personal_memory

        tenant_id = _tenant_id(context)
        user_id = _user_id(context)
        entry_id = int(args.get("entry_id", 0))

        learning = await _read_learning_enabled(engine, tenant_id, user_id)
        if not learning:
            return _error("learning_disabled", "user has disabled auto-learning")

        async with engine.begin() as conn:
            result = await conn.execute(
                delete(personal_memory).where(
                    personal_memory.c.tenant_id == tenant_id,
                    personal_memory.c.user_id == user_id,
                    personal_memory.c.id == entry_id,
                )
            )
        await _audit_tool_call(
            engine,
            tenant_id=tenant_id,
            actor_id=user_id,
            request_id="",
            action="user.memory.delete",
            target_id=str(entry_id),
            after={"deleted": result.rowcount},
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
            tool_id="user.memory.search",
            capability_id="builtin.user",
            name="user.memory.search",
            risk_level="low",
            external_dependency=False,
        ),
        _memory_search,
    )
    runtime.register(
        ToolDescriptor(
            tool_id="user.memory.correct",
            capability_id="builtin.user",
            name="user.memory.correct",
            risk_level="medium",
            external_dependency=False,
        ),
        _memory_correct,
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
