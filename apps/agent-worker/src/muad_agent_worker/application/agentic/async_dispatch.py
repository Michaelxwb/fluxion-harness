"""Async call dispatcher: operation is the anchor; the child Task is only an execution carrier."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from muad_contracts import CompletionMode, OperationStatus

from ...infrastructure.task_operation_client import (
    TaskOperationClient,
    operation_result_content,
)
from .async_results import operation_arguments

EnsureChild = Callable[[str, UUID, str, Mapping[str, object]], Awaitable[UUID | None]]


@dataclass(frozen=True, slots=True)
class AsyncCallState:
    operation_id: UUID
    status: OperationStatus
    task_id: UUID | None
    content: str | None = None

    @property
    def ready(self) -> bool:
        return self.content is not None


class AsyncCallDispatcher:
    """First execution and recovery share one idempotent entry point."""

    def __init__(
        self,
        operations: TaskOperationClient,
        *,
        ensure_child: EnsureChild | None = None,
    ) -> None:
        self._operations = operations
        self._ensure_child = ensure_child

    async def ensure(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, object],
        completion_mode: CompletionMode = CompletionMode.JOIN,
        recovered: bool = True,
        input_hash: str | None = None,
    ) -> AsyncCallState:
        normalized = operation_arguments(arguments)
        view = await self._operations.submit(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            execution_id=execution_id,
            call_id=call_id,
            tool_name=tool_name,
            arguments=normalized,
            completion_mode=completion_mode,
            input_hash=input_hash,
        )
        if view.result is not None:
            return AsyncCallState(
                operation_id=view.operation_id,
                status=view.status,
                task_id=view.task_id,
                content=operation_result_content(view.result),
            )
        task_id = view.task_id
        if task_id is None and self._ensure_child is not None:
            child_tool_name, child_arguments = tool_name, normalized
            if recovered:
                recovery = await self._operations.get_recovery(
                    tenant_id=tenant_id,
                    actor_user_id=actor_user_id,
                    execution_id=execution_id,
                    call_id=call_id,
                )
                child_tool_name = recovery.tool_name or tool_name
                child_arguments = recovery.arguments
            task_id = await self._ensure_child(
                call_id,
                view.operation_id,
                child_tool_name,
                child_arguments,
            )
        return AsyncCallState(
            operation_id=view.operation_id,
            status=view.status,
            task_id=task_id,
        )

__all__ = ["AsyncCallDispatcher", "AsyncCallState"]
