from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fluxion.runtime.context import RuntimeContext


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    action: str


@dataclass(frozen=True, slots=True)
class StepResult:
    success: bool
    output: str


@dataclass(frozen=True, slots=True)
class PlanningRunResult:
    replanned: bool
    executed_steps: tuple[str, ...]


Planner = Callable[[str, PlanStep | None], Awaitable[list[PlanStep]]]
StepExecutor = Callable[[PlanStep], Awaitable[StepResult]]


class PlanningAgentLoop:
    def __init__(self) -> None:
        self._active_plan_count = 0

    @property
    def active_plan_count(self) -> int:
        return self._active_plan_count

    async def run(
        self,
        context: RuntimeContext,
        *,
        objective: str,
        planner: Planner,
        executor: StepExecutor,
        max_replans: int = 8,
    ) -> PlanningRunResult:
        self._active_plan_count += 1
        try:
            return await self._run_plan(
                context, objective, planner, executor, max_replans=max_replans
            )
        finally:
            self._active_plan_count -= 1

    async def _run_plan(
        self,
        context: RuntimeContext,
        objective: str,
        planner: Planner,
        executor: StepExecutor,
        *,
        max_replans: int,
    ) -> PlanningRunResult:
        plan = await planner(objective, None)
        context.emit("plan.created", {"steps": [step.step_id for step in plan]})
        replanned = False
        replanned_count = 0
        executed: list[str] = []
        index = 0
        while index < len(plan):
            step = plan[index]
            result = await executor(step)
            executed.append(step.step_id)
            context.emit("plan.step", {"step_id": step.step_id, "success": result.success})
            if not result.success:
                if replanned_count >= max_replans:
                    context.emit("plan.limit_reached", {"failed_step_id": step.step_id})
                    break
                plan = await planner(objective, step)
                context.emit("plan.replanned", {"failed_step_id": step.step_id})
                replanned = True
                replanned_count += 1
                index = 0
                continue
            index += 1
        return PlanningRunResult(replanned=replanned, executed_steps=tuple(executed))
