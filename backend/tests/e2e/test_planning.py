from __future__ import annotations

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.registry import RegistryStore
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.runtime.planning import PlanningAgentLoop, PlanStep, StepResult
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver


@pytest.mark.asyncio
async def test_S_R20_plan_execute_replans_failed_step_in_current_execution(
    sqlite_store: RegistryStore,
) -> None:
    await seed_runtime_profile(sqlite_store)
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=InMemorySessionMemoryStore(),
    )
    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )
    loop = PlanningAgentLoop()
    attempts = 0
    executed_steps: list[str] = []

    async def planner(_objective: str, _failed_step: PlanStep | None) -> list[PlanStep]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return [
                PlanStep(step_id="fetch", action="fetch"),
                PlanStep(step_id="fail", action="fail"),
                PlanStep(step_id="finish", action="finish"),
            ]
        return [
            PlanStep(step_id="recover", action="recover"),
            PlanStep(step_id="finish", action="finish"),
        ]

    async def executor(step: PlanStep) -> StepResult:
        executed_steps.append(step.step_id)
        return StepResult(success=step.step_id != "fail", output=step.action)

    result = await loop.run(
        context,
        objective="完成长任务",
        planner=planner,
        executor=executor,
    )

    assert result.replanned is True
    assert executed_steps == ["fetch", "fail", "recover", "finish"]
    assert loop.active_plan_count == 0
    assert all(event.execution_id == context.snapshot.execution_id for event in context.trace)
    assert any(event.name == "plan.replanned" for event in context.trace)
