from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fluxion.runtime.agent import AgentRuntime
from fluxion.runtime.context import RequestContext, RuntimeContext


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    task_id: str
    tenant_id: str
    user_id: str
    runtime_profile_id: str
    session_id: str
    due_at: datetime
    approval_required: bool = True
    approved: bool = False


@dataclass(frozen=True, slots=True)
class ScheduledExecution:
    task_id: str
    context: RuntimeContext


class RuntimeScheduler:
    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime
        self._tasks: dict[str, ScheduledTask] = {}

    @property
    def local_execution_state_count(self) -> int:
        return len(self._tasks)

    def add_task(self, task: ScheduledTask) -> None:
        self._tasks[task.task_id] = task

    def approve(self, task_id: str) -> None:
        task = self._tasks[task_id]
        self._tasks[task_id] = ScheduledTask(
            task_id=task.task_id,
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            runtime_profile_id=task.runtime_profile_id,
            session_id=task.session_id,
            due_at=task.due_at,
            approval_required=task.approval_required,
            approved=True,
        )

    async def run_due(self, now: datetime) -> list[ScheduledExecution]:
        executions: list[ScheduledExecution] = []
        for task in list(self._tasks.values()):
            if not _can_run(task, now):
                continue
            context = await self._runtime.start_execution(_request_from_task(task))
            context.emit("scheduler.triggered", {"task_id": task.task_id})
            executions.append(ScheduledExecution(task_id=task.task_id, context=context))
            self._tasks.pop(task.task_id, None)
        return executions


def _can_run(task: ScheduledTask, now: datetime) -> bool:
    if task.due_at > now:
        return False
    return not task.approval_required or task.approved


def _request_from_task(task: ScheduledTask) -> RequestContext:
    return RequestContext(
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        runtime_profile_id=task.runtime_profile_id,
        session_id=task.session_id,
    )
