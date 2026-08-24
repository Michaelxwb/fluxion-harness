from __future__ import annotations

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.runtime.tools import ToolResultStatus, ToolRuntime
from fluxion.runtime.workflow import StubWorkflowEngine, WorkflowAdapter


@pytest.mark.asyncio
async def test_S_R08_workflow_adapter_returns_run_id_without_runtime_durable_state() -> None:
    context, runtime = await runtime_context()
    engine = StubWorkflowEngine(run_id="wf-run-1")
    adapter = WorkflowAdapter(workflow_id="weekly-report", engine=engine)
    tool_runtime = ToolRuntime()
    tool_runtime.register(adapter.descriptor, adapter.execute)

    result = await tool_runtime.call(
        context,
        "workflow.weekly-report.start",
        {"topic": "revenue"},
        user_grants={"workflow.weekly-report.start"},
        agent_allowlist={"workflow.weekly-report.start"},
        tenant_policy={"workflow.weekly-report.start"},
    )

    assert result.status is ToolResultStatus.STARTED
    assert result.run_id == "wf-run-1"
    assert runtime.local_durable_fact_count == 0
    assert adapter.local_durable_state_count == 0
    assert engine.started_requests[0].workflow_id == "weekly-report"
