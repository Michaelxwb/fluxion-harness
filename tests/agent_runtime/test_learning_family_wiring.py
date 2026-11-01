"""S-01 / S-02: task family propagation and frozen learning settings (TASK-001)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from types import SimpleNamespace

from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_agent_runtime.application.executor import _ExecutorEventEmitter
from muad_agent_runtime.application.run_events import DeltaWindow
from muad_agent_runtime.application.context_settings import frozen_learning_payload, learning_of
from muad_agent_runtime.application.executor import skill_family_from_tool_calls
from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.application.run_service import build_snapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import MemoryScope, UserMemory
from muad_contracts import ResolveDefinitionResponse, ResolvedAgent, ResolvedModel
from muad_contracts.platform_settings import MemoryPolicySettings

AGENT_ID = uuid.uuid4()


def test_skill_family_from_tool_calls() -> None:
    call = ModelToolCall(
        id="c1", name="execute_skill", arguments={"skill_key": "demo", "input": {}}
    )
    script = ModelToolCall(id="c2", name="run_skill_script", arguments={"skill_key": "demo"})
    unrelated = ModelToolCall(id="c3", name="load_skill", arguments={"skill_key": "demo"})
    assert skill_family_from_tool_calls([call]) == "skill:demo"
    assert skill_family_from_tool_calls([unrelated, script]) == "skill:demo"
    assert skill_family_from_tool_calls([unrelated]) is None
    assert skill_family_from_tool_calls([]) is None


async def _seed(tenant: str, user: uuid.UUID) -> None:
    expires = datetime.now(UTC) + timedelta(days=365)
    async with get_session_factory()() as session:
        session.add(MemoryScope(tenant_id=tenant, user_id=user, revision=1))
        rows = [
            ("exp.skill", "skill:demo", "ACTIVE", "skill 族经验：逐项核对"),
            (f"exp.agent", f"agent:{AGENT_ID}", "ACTIVE", "agent 族经验：先确认目标"),
            ("exp.proposed", "skill:demo", "PROPOSED", "尚未激活"),
        ]
        for key, signature, state, value in rows:
            session.add(
                UserMemory(
                    tenant_id=tenant,
                    user_id=user,
                    memory_key=key,
                    category="PROCEDURE",
                    content_json={"value": value},
                    author="AGENT",
                    confidence=2,
                    explicit_request=False,
                    expires_at=expires,
                    activation_state=state,
                    task_signature=signature,
                    trigger_text="不会命中这个问题",
                )
            )
        await session.commit()


async def _keys(tenant: str, user: uuid.UUID, family: str | None, *, enabled: bool) -> list[str]:
    factory = get_session_factory()
    cache = MemoryCandidateCache(
        MemoryQueries(factory),
        scope=MemoryReadScope(tenant_id=tenant, user_id=user),
        run_id=uuid.uuid4(),
    )
    loader = MemoryContextLoader(
        cache,
        settings=MemoryPolicySettings(),
        query="处理一下",
        learning_enabled=enabled,
    )
    if family is not None:
        loader.set_task_family(family)
    component = await loader.refresh()
    assert component is not None
    return [item.record.memory_key for item in component.block.items]


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.commit()


async def test_s01_family_lane_follows_skill_and_agent_fallback(database_guard: None) -> None:
    tenant, user = f"family-{uuid.uuid4()}", uuid.uuid4()
    await _seed(tenant, user)
    try:
        skill_keys = await _keys(tenant, user, "skill:demo", enabled=True)
        assert "exp.skill" in skill_keys
        assert "exp.agent" not in skill_keys
        assert "exp.proposed" not in skill_keys

        agent_keys = await _keys(tenant, user, f"agent:{AGENT_ID}", enabled=True)
        assert "exp.agent" in agent_keys
        assert "exp.skill" not in agent_keys

        # learning 关闭：经验一律不注入。
        disabled = await _keys(tenant, user, "skill:demo", enabled=False)
        assert "exp.skill" not in disabled and "exp.agent" not in disabled
    finally:
        await _cleanup(tenant)


def test_s02_freezes_learning_settings_into_snapshot() -> None:
    payload = frozen_learning_payload(
        {"learning": {"learning_enabled": True, "retrieval_min_score": 0.7}}
    )
    assert payload["learning_enabled"] is True
    assert payload["retrieval_min_score"] == 0.7
    assert payload["retrieval_preference_items"] == 3

    resolved = ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=AGENT_ID, key="demo", revision=1, instructions="fixed", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://127.0.0.1:1"
        ),
    )
    snapshot = build_snapshot(
        run_id=uuid.uuid4(), tenant_id="t", resolved=resolved, learning=payload
    )
    frozen = learning_of(snapshot.policy_json)
    assert frozen.enabled is True
    assert frozen.min_score == 0.7

    # 旧 Snapshot 缺 learning 段：回落默认（关闭）。
    assert learning_of({}).enabled is False


async def test_emitter_switches_task_family_on_skill_turn() -> None:
    """真实发射器路径：assistant 回合带 skill 工具调用 → 设置 loader 族。"""
    calls: list[str] = []

    class _Loader:
        def set_task_family(self, family: str) -> None:
            calls.append(family)

    emitter = _ExecutorEventEmitter(
        queue=__import__("asyncio").Queue(),
        window=DeltaWindow(flush_interval_sec=0.05, flush_bytes=4096),
        request=SimpleNamespace(),
        model_trace=None,
        memory=SimpleNamespace(loader=_Loader()),
    )

    async def _noop(event) -> None:
        return None

    emitter.emit = _noop  # type: ignore[method-assign]
    await emitter.on_assistant_turn(
        ModelMessage(
            role=ModelRole.ASSISTANT,
            content="",
            tool_calls=[ModelToolCall(id="c1", name="execute_skill", arguments={"skill_key": "demo"})],
        )
    )
    assert calls == ["skill:demo"]
