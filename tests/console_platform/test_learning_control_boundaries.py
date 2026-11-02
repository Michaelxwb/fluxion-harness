"""TASK-009 / E-05：止损/暂停的幂等与边界。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from muad_agent_runtime.application.learning_admin_service import LearningAdminService
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    LearningFamilyControl,
    UserMemory,
    UserMemoryHistory,
)

from tests.agent_runtime.learning_wiring_support import cleanup, seed_runs


class _Settings:
    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=1, settings={"learning": {"learning_enabled": True}}
        )


async def test_e05_stop_is_idempotent_and_control_is_unique(
    database_guard: None,
) -> None:
    tenant, user = f"boundary-{uuid.uuid4()}", uuid.uuid4()
    run_id = (await seed_runs(tenant, user, 1))[0]
    async with get_session_factory()() as session:
        conversation_id = await session.scalar(
            sa.text("SELECT conversation_id FROM runtime.run_record WHERE id=:id"), {"id": run_id}
        )
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=user,
                memory_key="exp.stop",
                category="PROCEDURE",
                content_json={"value": "做法"},
                author="AGENT",
                authority="AGENT_INFERRED",
                confidence=2,
                explicit_request=False,
                expires_at=datetime.now(UTC) + timedelta(days=365),
                activation_state="ACTIVE",
                task_signature="skill:demo",
                trigger_text="触发",
            )
        )
        session.add(
            CanonicalEvent(
                tenant_id=tenant,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=999,
                event_type="MEMORY_INJECTED",
                payload_json={
                    "items": [
                        {"memory_key": "exp.stop", "version": 1, "score": 1.0, "channel": "FAMILY"}
                    ]
                },
            )
        )
        await session.commit()
    service = LearningAdminService(get_session_factory(), settings_client=_Settings())
    try:
        actor = uuid.uuid4()
        first = await service.stop_run(tenant, run_id, operator=str(actor), reason="止损")
        second = await service.stop_run(tenant, run_id, operator=str(actor), reason="止损")
        assert first[0]["activation_state"] == "RETIRED"
        assert second[0]["status"] == "UNCHANGED"
        async with get_session_factory()() as session:
            history = await session.scalar(
                sa.select(sa.func.count())
                .select_from(UserMemoryHistory)
                .where(UserMemoryHistory.tenant_id == tenant)
            )
        assert int(history or 0) == 1

        await service.set_family_control(
            tenant, "skill:demo", paused=True, reason="止损", actor=actor
        )
        await service.set_family_control(
            tenant, "skill:demo", paused=False, reason="恢复", actor=actor
        )
        async with get_session_factory()() as session:
            rows = (
                await session.scalars(
                    sa.select(LearningFamilyControl).where(
                        LearningFamilyControl.tenant_id == tenant
                    )
                )
            ).all()
        assert len(rows) == 1 and rows[0].paused is False
    finally:
        await cleanup(tenant)
