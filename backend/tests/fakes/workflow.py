"""测试 Fake：StubWorkflowEngine（TASK-009 / WF-01 移出主模块）。

生产主模块 `runtime/workflow.py` 只保留 `ResilientWorkflowEngine` / `WorkflowAdapter`；
本 Stub 供测试与 Adapter 契约验证使用，不进入生产 Runtime 契约。
"""

from __future__ import annotations

import asyncio

from fluxion.contracts.workflow import (
    WorkflowExecutionHistory,
    WorkflowRunStatus,
    WorkflowStartRequest,
    WorkflowStartResult,
)


class StubWorkflowEngine:
    def __init__(self, *, run_id: str) -> None:
        self._run_id = run_id
        self.started_requests: list[WorkflowStartRequest] = []
        self.resumed: list[str] = []
        self.signals: list[tuple[str, str, object]] = []
        self.cancelled: list[str] = []
        self._status = "running"

    async def start(self, request: WorkflowStartRequest) -> WorkflowStartResult:
        self.started_requests.append(request)
        return WorkflowStartResult(run_id=self._run_id)

    async def resume(self, run_id: str) -> WorkflowRunStatus:
        self.resumed.append(run_id)
        return WorkflowRunStatus(run_id=run_id, status=self._status)

    async def signal(self, run_id: str, name: str, payload: object) -> None:
        self.signals.append((run_id, name, payload))

    async def cancel(self, run_id: str, *, timeout: float) -> None:
        self.cancelled.append(run_id)
        self._status = "cancelled"

    async def get_status(self, run_id: str) -> WorkflowRunStatus:
        return WorkflowRunStatus(run_id=run_id, status=self._status)

    async def await_result(self, run_id: str, *, timeout: float) -> object:
        await asyncio.sleep(0)
        return {"stub": True, "run_id": run_id}

    async def get_execution_history(self, run_id: str) -> WorkflowExecutionHistory:
        return WorkflowExecutionHistory(run_id=run_id, status=self._status)
