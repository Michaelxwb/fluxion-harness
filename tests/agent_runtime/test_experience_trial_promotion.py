"""S-12: TRIAL start, automatic promotion and retirement (TASK-008)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from muad_agent_runtime.application.learning_activation import (
    ACTIVE,
    RETIRED,
    TRIAL,
    ActivationPolicy,
    LearningActivation,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    EvalReport,
    MemoryScope,
    UserMemory,
    UserMemoryHistory,
)


async def _seed(tenant: str, user: uuid.UUID, key: str, *, category: str) -> None:
    expires = datetime.now(UTC) + timedelta(days=365)
    async with get_session_factory()() as session:
        existing = await session.scalar(
            sa.select(MemoryScope).where(
                MemoryScope.tenant_id == tenant, MemoryScope.user_id == user
            )
        )
        if existing is None:
            session.add(MemoryScope(tenant_id=tenant, user_id=user, revision=1))
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=user,
                memory_key=key,
                category=category,
                content_json={"value": "验证方法：核对数量"},
                author="AGENT",
                confidence=2,
                explicit_request=False,
                expires_at=expires,
                activation_state="PROPOSED",
                task_signature="skill:demo",
                trigger_text="批量客户逐项处理",
            )
        )
        session.add(
            EvalReport(
                tenant_id=tenant,
                task_family="skill:demo",
                experience_key=key,
                experience_user_id=user,
                base_config_hash="base-1",
                metrics_json={},
                decision_json={"status": "PASSED"},
                report_json={},
                content_hash=f"hash-{uuid.uuid4()}",
                version=1,
            )
        )
        await session.commit()


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalReport).where(EvalReport.tenant_id == tenant))
        await session.execute(
            sa.delete(UserMemoryHistory).where(UserMemoryHistory.tenant_id == tenant)
        )
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(MemoryScope).where(MemoryScope.tenant_id == tenant))
        await session.execute(
            sa.text("DELETE FROM control.config_audit_log WHERE tenant_id=:t"), {"t": tenant}
        )
        await session.commit()


async def test_s12_auto_promotion_and_retirement_follow_trial_signals(database_guard: None) -> None:
    tenant, user = f"trial-{uuid.uuid4()}", uuid.uuid4()
    await _seed(tenant, user, "exp.good", category="PROCEDURE")
    await _seed(tenant, user, "exp.bad", category="PROCEDURE")
    await _seed(tenant, user, "exp.manual", category="VERIFICATION")
    service = LearningActivation(policy=ActivationPolicy(trial_runs=10, trial_max_regression=0.05))
    try:
        trial = await service.start_trial(
            tenant, user, "exp.good", base_config_hash="base-1"
        )
        assert trial.activation_state == TRIAL

        promoted = await service.evaluate_trial(
            tenant,
            user,
            "exp.good",
            baseline_corrections=2,
            trial_corrections=2,
            base_config_hash="base-1",
        )
        assert promoted.activation_state == ACTIVE

        await service.start_trial(tenant, user, "exp.bad", base_config_hash="base-1")
        retired = await service.evaluate_trial(
            tenant,
            user,
            "exp.bad",
            baseline_corrections=0,
            trial_corrections=5,
            base_config_hash="base-1",
        )
        assert retired.activation_state == RETIRED

        manual = await service.start_trial(
            tenant, user, "exp.manual", base_config_hash="base-1"
        )
        assert manual.status == "MANUAL_REQUIRED"
        assert manual.activation_state == "PROPOSED"
    finally:
        await _cleanup(tenant)
