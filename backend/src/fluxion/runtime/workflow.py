from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolDescriptor, ToolResult


@dataclass(frozen=True, slots=True)
class WorkflowStartRequest:
    workflow_id: str
    tenant_id: str
    user_id: str
    execution_id: str
    trace_id: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class WorkflowStartResult:
    run_id: str
    status: str = "started"


class WorkflowEngine(Protocol):
    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult: ...


class WorkflowAdapter:
    def __init__(self, *, workflow_id: str, engine: WorkflowEngine) -> None:
        if not workflow_id.strip():
            raise ValueError("workflow_id is required")
        self._workflow_id = workflow_id
        self._engine = engine

    @property
    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            tool_id=f"workflow.{self._workflow_id}.start",
            capability_id=f"workflow.{self._workflow_id}",
            name=f"workflow.{self._workflow_id}.start",
            external_dependency=True,
        )

    @property
    def local_durable_state_count(self) -> int:
        return 0

    async def execute(
        self,
        context: RuntimeContext,
        arguments: dict[str, object],
    ) -> ToolResult:
        result = await self._engine.start(
            WorkflowStartRequest(
                workflow_id=self._workflow_id,
                tenant_id=context.snapshot.tenant_id,
                user_id=context.snapshot.user_id,
                execution_id=context.snapshot.execution_id,
                trace_id=context.snapshot.trace_id,
                arguments=arguments,
            )
        )
        return ToolResult.started(result.run_id, result.status)


class StubWorkflowEngine:
    def __init__(self, *, run_id: str) -> None:
        self._run_id = run_id
        self.started_requests: list[WorkflowStartRequest] = []

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult:
        self.started_requests.append(request)
        return WorkflowStartResult(run_id=self._run_id)
