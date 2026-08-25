from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.registry import RegistryStore
from fluxion.runtime import AgentRuntime
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver
from fluxion.runtime.scheduler import RuntimeScheduler, ScheduledTask


@pytest.mark.asyncio
async def test_S_R19_scheduler_runs_independent_approved_executions(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=InMemorySessionMemoryStore(),
    )
    scheduler = RuntimeScheduler(runtime)
    due = datetime.now(UTC) - timedelta(seconds=1)

    scheduler.add_task(
        ScheduledTask(
            task_id="approved",
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
            due_at=due,
            approved=True,
        )
    )
    scheduler.add_task(
        ScheduledTask(
            task_id="blocked",
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
            due_at=due,
            approval_required=True,
            approved=False,
        )
    )
    first_run = await scheduler.run_due(datetime.now(UTC))
    scheduler.approve("blocked")
    second_run = await scheduler.run_due(datetime.now(UTC))

    execution_ids = [item.context.snapshot.execution_id for item in [*first_run, *second_run]]
    assert len(execution_ids) == 2
    assert len(set(execution_ids)) == 2
    assert {item.task_id for item in first_run} == {"approved"}
    assert {item.task_id for item in second_run} == {"blocked"}
    assert scheduler.local_execution_state_count == 0
