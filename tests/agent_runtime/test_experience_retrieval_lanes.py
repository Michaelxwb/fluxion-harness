"""S-13: experience hits for non-standard tasks and family switch (TASK-007)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import MemoryScope, UserMemory
from muad_contracts.platform_settings import MemoryPolicySettings


async def _seed(tenant: str, user: uuid.UUID) -> None:
    expires = datetime.now(UTC) + timedelta(days=365)
    async with get_session_factory()() as session:
        session.add(MemoryScope(tenant_id=tenant, user_id=user, revision=1))
        rows = [
            ("exp.proc.active", "PROCEDURE", "ACTIVE", "skill:demo", "批量客户逐项处理 多个目标 核对数量", "逐个目标调用 demo 并核对数量"),
            ("exp.proc.proposed", "PROCEDURE", "PROPOSED", "skill:demo", "批量客户逐项处理 多个目标 核对数量", "尚未激活的经验"),
            ("exp.proc.other", "PROCEDURE", "ACTIVE", "skill:other", "处理发票报销", "处理发票时先核对金额"),
            ("pref.briefing", "PREFERENCE", "ACTIVE", None, None, "简报先给结论"),
        ]
        for key, category, state, signature, trigger, value in rows:
            if category == "PREFERENCE":
                session.add(
                    UserMemory(
                        tenant_id=tenant,
                        user_id=user,
                        memory_key=key,
                        category=category,
                        content_json={"value": value},
                        author="USER",
                        explicit_request=True,
                        confirmation_event_id=uuid.uuid4(),
                    )
                )
            else:
                session.add(
                    UserMemory(
                        tenant_id=tenant,
                        user_id=user,
                        memory_key=key,
                        category=category,
                        content_json={"value": value},
                        author="AGENT",
                        confidence=2,
                        explicit_request=False,
                        expires_at=expires,
                        activation_state=state,
                        task_signature=signature,
                        trigger_text=trigger,
                    )
                )
        await session.commit()


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.commit()


def _loader(tenant: str, user: uuid.UUID, query: str) -> MemoryContextLoader:
    factory = get_session_factory()
    cache = MemoryCandidateCache(
        MemoryQueries(factory),
        scope=MemoryReadScope(tenant_id=tenant, user_id=user),
        run_id=uuid.uuid4(),
    )
    return MemoryContextLoader(cache, settings=MemoryPolicySettings(), query=query)


async def test_s13_goal_match_hits_experience_without_skill(database_guard: None) -> None:
    tenant, user = f"lanes-{uuid.uuid4()}", uuid.uuid4()
    await _seed(tenant, user)
    try:
        loader = _loader(tenant, user, "批量客户逐项处理这些客户")
        component = await loader.refresh()
        assert component is not None
        keys = [item.record.memory_key for item in component.block.items]
        assert "exp.proc.active" in keys
        assert "exp.proc.proposed" not in keys
        assert "exp.proc.other" not in keys
    finally:
        await _cleanup(tenant)


async def test_s13_family_switch_after_skill_execution(database_guard: None) -> None:
    tenant, user = f"lanes-{uuid.uuid4()}", uuid.uuid4()
    await _seed(tenant, user)
    try:
        loader = _loader(tenant, user, "处理一下")
        before = await loader.refresh()
        assert before is not None
        assert "exp.proc.active" not in [item.record.memory_key for item in before.block.items]

        loader.set_task_family("skill:demo")
        after = await loader.refresh()
        assert after is not None
        keys = [item.record.memory_key for item in after.block.items]
        assert "exp.proc.active" in keys
        assert "exp.proc.proposed" not in keys
        assert keys.count("exp.proc.active") == 1
    finally:
        await _cleanup(tenant)
