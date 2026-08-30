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


class SchedulerProfileError(RuntimeError):
    """production profile 禁止本地 `_tasks` scheduler 承载生产任务（P0-4/REQ-SCH-001）。"""

    code = "scheduler_profile_violation"


class RuntimeScheduler:
    """本地任务调度（进程内 `_tasks` dict）——仅 test/dev profile 放行。

    P0-4（migration 偏差 / E-08）：生产任务事实必须外置（durable_task 表），
    本地实现不得承载生产任务——production profile 构造即 fail-fast，不静默降级。
    """

    def __init__(self, runtime: AgentRuntime, *, profile: str = "dev") -> None:
        if profile == "production":
            raise SchedulerProfileError(
                "RuntimeScheduler 本地 _tasks 实现禁止在 production profile 启用"
                "（生产任务事实须外置 durable_task 表；仅 test/dev 放行，REQ-SCH-001）"
            )
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
