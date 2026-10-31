"""S-06 / S-08: activation gate, audit, rollback and freeze (TASK-008)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from muad_agent_runtime.application.learning_activation import (
    ACTIVE,
    RETIRED,
    ActivationPolicy,
    LearningActivation,
)
from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    EvalReport,
    MemoryScope,
    UserMemory,
)
from muad_contracts.platform_settings import MemoryPolicySettings

TENANT = ""


def _seed_rows(tenant: str, user: uuid.UUID, key: str, *, category="PROCEDURE", state="PROPOSED") -> None:
    expires = datetime.now(UTC) + timedelta(days=365)
    with_state = state
    async def run() -> None:
        async with get_session_factory()() as session:
            session.add(
                UserMemory(
                    tenant_id=tenant,
                    user_id=user,
                    memory_key=key,
                    category=category,
                    content_json={"value": "逐个目标调用 demo 并核对数量"},
                    author="AGENT",
                    confidence=2,
                    explicit_request=False,
                    expires_at=expires,
                    activation_state=with_state,
                    task_signature="skill:demo",
                    trigger_text="批量客户逐项处理",
                )
            )
            await session.commit()
    return run()


async def _report(tenant: str, key: str, status: str) -> None:
    async with get_session_factory()() as session:
        session.add(
            EvalReport(
                tenant_id=tenant,
                task_family="skill:demo",
                experience_key=key,
                base_config_hash="base-1",
                metrics_json={},
                decision_json={"status": status},
                report_json={},
                content_hash=f"hash-{uuid.uuid4()}",
                version=1,
            )
        )
        await session.commit()


async def _loader(tenant: str, user: uuid.UUID):
    factory = get_session_factory()
    scope = MemoryReadScope(tenant_id=tenant, user_id=user)
    cache = MemoryCandidateCache(MemoryQueries(factory), scope=scope, run_id=uuid.uuid4())
    loader = MemoryContextLoader(
        cache,
        settings=MemoryPolicySettings(),
        query="批量客户逐项处理",
    )
    loader.set_task_family("skill:demo")
    return loader


async def _keys(loader: MemoryContextLoader) -> list[str]:
    component = await loader.refresh()
    assert component is not None
    return [item.record.memory_key for item in component.block.items]


async def _audits(tenant: str) -> list[dict]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT action, after_json FROM control.config_audit_log"
                    " WHERE tenant_id=:t AND resource_type='MEMORY_EXPERIENCE'"
                    " ORDER BY create_time"
                ),
                {"t": tenant},
            )
        ).all()
    return [{"action": row[0], "after": row[1]} for row in rows]


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalReport).where(EvalReport.tenant_id == tenant))
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.execute(
            sa.text("DELETE FROM control.config_audit_log WHERE tenant_id=:t"), {"t": tenant}
        )
        await session.commit()


async def test_s06_activation_gate_requires_replay_and_freezes_existing_runs(
    database_guard: None,
) -> None:
    tenant, user = f"act-{uuid.uuid4()}", uuid.uuid4()
    await _seed_rows(tenant, user, "exp.rejected")
    await _seed_rows(tenant, user, "exp.ok")
    await _report(tenant, "exp.rejected", "REJECTED")
    await _report(tenant, "exp.ok", "PASSED")
    async with get_session_factory()() as session:
        session.add(MemoryScope(tenant_id=tenant, user_id=user, revision=1))
        await session.commit()
    service = LearningActivation(policy=ActivationPolicy())
    try:
        before_loader = await _loader(tenant, user)
        assert "exp.ok" not in await _keys(before_loader)

        rejected = await service.activate_manually(
            tenant, user, "exp.rejected", base_config_hash="base-1", operator="alice"
        )
        assert rejected.status == "EXPERIENCE_EVAL_BELOW_THRESHOLD"
        assert rejected.activation_state == "PROPOSED"

        activated = await service.activate_manually(
            tenant, user, "exp.ok", base_config_hash="base-1", operator="alice"
        )
        assert activated.activation_state == ACTIVE

        # 已创建的 Run 冻结激活集：旧 loader 不因激活而开始注入。
        assert "exp.ok" not in await _keys(before_loader)

        new_loader = await _loader(tenant, user)
        assert "exp.ok" in await _keys(new_loader)

        audits = await _audits(tenant)
        assert [audit["action"] for audit in audits] == ["ACTIVATE"]
        assert audits[0]["after"]["operator"] == "alice"
    finally:
        await _cleanup(tenant)


async def test_s08_retirement_audits_and_stops_injection(database_guard: None) -> None:
    tenant, user = f"act-{uuid.uuid4()}", uuid.uuid4()
    await _seed_rows(tenant, user, "exp.ok")
    await _report(tenant, "exp.ok", "PASSED")
    async with get_session_factory()() as session:
        session.add(MemoryScope(tenant_id=tenant, user_id=user, revision=1))
        await session.commit()
    service = LearningActivation()
    try:
        await service.activate_manually(
            tenant, user, "exp.ok", base_config_hash="base-1", operator="alice"
        )
        retired = await service.retire(
            tenant, user, "exp.ok", reason="degraded", operator="bob"
        )
        assert retired.activation_state == RETIRED

        audits = await _audits(tenant)
        assert [audit["action"] for audit in audits] == ["ACTIVATE", "RETIRE"]
        assert audits[1]["after"]["operator"] == "bob"
        assert audits[1]["after"]["reason"] == "degraded"

        async with get_session_factory()() as session:
            row = await session.scalar(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == tenant, UserMemory.memory_key == "exp.ok"
                )
            )
        assert row is not None and row.is_deleted is False
        assert row.activation_state == RETIRED

        loader = await _loader(tenant, user)
        assert "exp.ok" not in await _keys(loader)
    finally:
        await _cleanup(tenant)
