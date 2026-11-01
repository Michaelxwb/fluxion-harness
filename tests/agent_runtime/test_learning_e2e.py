"""S-07: end-to-end learning loop through real scheduler and loader (TASK-004)."""

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
from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_injection_log import MemoryInjectionLog
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    EvalCase,
    UserMemory,
)
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.learning_wiring_support import (
    Settings,
    cleanup,
    seed_credential,
    seed_runs,
    transport,
)


async def test_s07_three_runs_learn_and_inject_on_next_request(database_guard: None) -> None:
    tenant, user = f"e2e-{uuid.uuid4()}", uuid.uuid4()
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
        await supervisor.run_once()

        async with factory() as session:
            experience = await session.scalar(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == tenant, UserMemory.category == "PROCEDURE"
                )
            )
            cases = await session.scalar(
                sa.select(sa.func.count()).select_from(EvalCase).where(EvalCase.tenant_id == tenant)
            )
            run = await session.execute(
                sa.text(
                    "SELECT conversation_id FROM runtime.run_record"
                    " WHERE tenant_id=:t LIMIT 1"
                ),
                {"t": tenant},
            )
            conversation_id = run.scalar_one()
        assert experience is not None and experience.activation_state == "ACTIVE"
        assert int(cases or 0) == 3

        # 第 4 次请求（同族）的注入与归因包含该经验。
        run_id = uuid.uuid4()
        log = MemoryInjectionLog(
            factory, tenant_id=tenant, conversation_id=conversation_id, run_id=run_id
        )
        cache = MemoryCandidateCache(
            MemoryQueries(factory),
            scope=MemoryReadScope(tenant_id=tenant, user_id=user),
            run_id=run_id,
        )
        loader = MemoryContextLoader(
            cache,
            settings=MemoryPolicySettings(),
            query="处理一下",
            on_attribution=log.record,
            learning_enabled=True,
        )
        loader.set_task_family(experience.task_signature or "")
        component = await loader.refresh()
        assert component is not None
        keys = [item.record.memory_key for item in component.block.items]
        assert experience.memory_key in keys

        async with factory() as session:
            event = await session.scalar(
                sa.select(CanonicalEvent).where(
                    CanonicalEvent.tenant_id == tenant,
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.event_type == "MEMORY_INJECTED",
                )
            )
        assert event is not None
        injected = {item["memory_key"] for item in event.payload_json["items"]}
        assert experience.memory_key in injected
    finally:
        await cleanup(tenant)
