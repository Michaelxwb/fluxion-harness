"""按 `task_type` 分派执行器：一种模式一个执行器，执行路径不共用 Skill 语义。

REMINDER 只产出固定文本；SKILL（含其内部 BATCH 父任务）按冻结 Snapshot 执行 Skill；
AGENTIC 走中立 Kernel 的 Worker 适配器（TASK-019）。装配根没接 AGENTIC 执行器时显式失败——
绝不把 AGENTIC 任务降级成 Skill 执行。
"""

from __future__ import annotations

from typing import Any

from muad_contracts import TaskType

from ..application.batch_fanout import TASK_TYPE_BATCH
from ..infrastructure.models.task import TaskExecution
from .executor import TaskExecutionError, TaskExecutorProtocol

AGENTIC_EXECUTOR_UNAVAILABLE = "AGENTIC_EXECUTOR_UNAVAILABLE"


class TaskTypeExecutor:
    """生产装配的执行入口：把 Task 分派给对应类型的执行器。"""

    def __init__(
        self,
        *,
        reminder: TaskExecutorProtocol,
        skill: TaskExecutorProtocol,
        agentic: TaskExecutorProtocol | None = None,
    ) -> None:
        self._reminder = reminder
        self._skill = skill
        self._agentic = agentic

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        task_type = task.task_type
        if task_type == str(TaskType.REMINDER):
            return await self._reminder.execute(task)
        if task_type in (str(TaskType.SKILL), TASK_TYPE_BATCH):
            return await self._skill.execute(task)
        if task_type == str(TaskType.AGENTIC):
            if self._agentic is None:
                raise TaskExecutionError(
                    AGENTIC_EXECUTOR_UNAVAILABLE,
                    "no agentic execution adapter is wired into this worker process",
                    retryable=False,
                )
            return await self._agentic.execute(task)
        raise TaskExecutionError(
            "TASK_TYPE_UNSUPPORTED",
            f"no executor is wired for task_type={task_type!r}",
            retryable=False,
        )
