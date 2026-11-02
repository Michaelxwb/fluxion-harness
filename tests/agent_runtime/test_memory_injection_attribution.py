"""S-02: memory injection attribution with selection-hash dedupe (TASK-003)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_injection_log import MemoryInjectionLog
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    MemoryScope,
    UserMemory,
)
from muad_contracts.platform_settings import MemoryPolicySettings


async def _seed(tenant: str, user: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        session.add(MemoryScope(tenant_id=tenant, user_id=user, revision=1))
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=user,
                memory_key="pref.briefing",
                category="PREFERENCE",
                content_json={"value": "简报先给结论"},
                author="USER",
                explicit_request=True,
                confirmation_event_id=uuid.uuid4(),
            )
        )
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=user,
                memory_key="fact.customer-format",
                category="EXPLICIT",
                content_json={"value": "客户报表用表格"},
                author="USER",
                explicit_request=True,
                confirmation_event_id=uuid.uuid4(),
            )
        )
        await session.commit()


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant))
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


async def _events(tenant: str, run_id: uuid.UUID) -> list[CanonicalEvent]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.scalars(
                    sa.select(CanonicalEvent)
                    .where(
                        CanonicalEvent.tenant_id == tenant,
                        CanonicalEvent.run_id == run_id,
                        CanonicalEvent.event_type == "MEMORY_INJECTED",
                    )
                    .order_by(CanonicalEvent.seq)
                )
            ).all()
        )


async def test_s02_attribution_matches_selection_and_dedupes_by_hash(
    database_guard: None,
) -> None:
    tenant = f"attrib-{uuid.uuid4()}"
    user, conversation_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(id=conversation_id, tenant_id=tenant, user_id=user, agent_id=uuid.uuid4())
        )
        await session.commit()
    await _seed(tenant, user)
    try:
        factory = get_session_factory()
        queries = MemoryQueries(factory)
        scope = MemoryReadScope(tenant_id=tenant, user_id=user)
        cache = MemoryCandidateCache(queries, scope=scope, run_id=run_id)
        log = MemoryInjectionLog(
            factory, tenant_id=tenant, conversation_id=conversation_id, run_id=run_id
        )
        loader = MemoryContextLoader(
            cache,
            settings=MemoryPolicySettings(),
            query="客户报表",
            on_attribution=log.record,
        )

        component = await loader.refresh()
        assert component is not None
        selected = {(item.record.memory_key, item.record.version) for item in component.block.items}
        rows = await _events(tenant, run_id)
        assert len(rows) == 1
        payload = rows[0].payload_json
        attributed = {(item["memory_key"], item["version"]) for item in payload["items"]}
        assert attributed == selected
        assert all(item["decision"] == "INJECTED" for item in payload["items"])
        assert all(isinstance(item["score"], (int, float)) for item in payload["items"])
        assert payload["dropped"] == {"count": 0, "bytes": 0}
        assert payload["diagnostics"]["learning_enabled"] is True
        assert isinstance(payload["diagnostics"]["min_score"], (int, float))
        assert all(
            item["channel"] in {"PREFERENCE", "TEXT", "FAMILY", "TASK_STATE"}
            for item in payload["items"]
        )
        assert payload["selection_hash"]

        # 选择未变化：第二次刷新不新增事件。
        second = await loader.refresh()
        assert second is not None
        assert len(await _events(tenant, run_id)) == 1

        # 记忆版本变化 ⇒ 选择 hash 变化 ⇒ 追加一条归因。
        async with factory() as session:
            await session.execute(
                sa.update(UserMemory)
                .where(
                    UserMemory.tenant_id == tenant,
                    UserMemory.memory_key == "fact.customer-format",
                )
                .values(version=2)
            )
            await session.execute(
                sa.update(MemoryScope)
                .where(MemoryScope.tenant_id == tenant, MemoryScope.user_id == user)
                .values(revision=2)
            )
            await session.commit()
        third = await loader.refresh()
        assert third is not None
        rows = await _events(tenant, run_id)
        assert len(rows) == 2
        assert rows[0].payload_json["selection_hash"] != rows[1].payload_json["selection_hash"]
    finally:
        await _cleanup(tenant)
