"""S-05: replay pass starts TRIAL and the observation window promotes (TASK-004)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_agent_runtime.application.learning_activation import LearningActivation
from muad_agent_runtime.application.learning_case_runner import (
    ProductionCaseRunner,
    database_credentials_resolver,
)
from muad_agent_runtime.application.learning_replay import LearningReplay, ReplayLimits
from muad_agent_runtime.application.learning_scheduler import LearningSupervisor
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import UserMemory
from muad_contracts import RunStatus

from tests.agent_runtime.learning_wiring_support import (
    Settings,
    cleanup,
    seed_credential,
    seed_runs,
    transport,
)


async def _experience(tenant: str) -> UserMemory | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(UserMemory).where(
                UserMemory.tenant_id == tenant, UserMemory.category == "PROCEDURE"
            )
        )


async def test_s05_replay_pass_starts_trial_then_promotes(database_guard: None) -> None:
    tenant, user = f"trial-{uuid.uuid4()}", uuid.uuid4()
    await seed_runs(tenant, user, 3)
    await seed_credential(tenant)
    factory = get_session_factory()
    replay = LearningReplay(
        factory,
        ProductionCaseRunner(
            factory,
            resolve_credentials=database_credentials_resolver(factory),
            transport=transport(),
        ),
        ReplayLimits(min_cases=1, max_cases=50),
    )
    supervisor = LearningSupervisor(
        factory,
        settings_client=Settings(tenant),
        replay=replay,
        activation=LearningActivation(factory),
    )
    try:
        await supervisor.run_once()
        row = await _experience(tenant)
        assert row is not None and row.activation_state == "TRIAL"

        await supervisor.run_once()
        row = await _experience(tenant)
        assert row is not None and row.activation_state == "ACTIVE"

        async with factory() as session:
            actions = (
                await session.execute(
                    sa.text(
                        "SELECT action FROM control.config_audit_log"
                        " WHERE tenant_id=:t AND resource_type='MEMORY_EXPERIENCE'"
                        " ORDER BY create_time"
                    ),
                    {"t": tenant},
                )
            ).scalars().all()
        assert list(actions) == ["TRIAL_START", "TRIAL_PROMOTE"]
    finally:
        await cleanup(tenant)
