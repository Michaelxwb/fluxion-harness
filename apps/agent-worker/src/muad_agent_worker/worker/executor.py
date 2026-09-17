from __future__ import annotations

from typing import Any, Protocol

from ..infrastructure.models.task import TaskExecution


class TaskExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskExecutorProtocol(Protocol):
    async def execute(self, task: TaskExecution) -> dict[str, Any]: ...


class SkeletonTaskExecutor:
    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        return {"skeleton": True}
