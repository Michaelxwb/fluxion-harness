from __future__ import annotations

import pytest
from tests.runtime_helpers import minimal_tool_context

from fluxion.runtime.tools import ToolResultStatus, ToolRuntime
from fluxion.runtime.workflow import WorkflowAdapter
from tests.fakes.workflow import StubWorkflowEngine


@pytest.mark.asyncio
async def test_S_R08_workflow_adapter_returns_run_id_without_runtime_durable_state() -> None:
    context = minimal_tool_context(
        {
            "agent_tools": ["workflow.weekly-report.start"],
            "user_tools": ["workflow.weekly-report.start"],
            "tenant_tools": ["workflow.weekly-report.start"],
        }
    )
    engine = StubWorkflowEngine(run_id="wf-run-1")
    adapter = WorkflowAdapter(workflow_id="weekly-report", engine=engine)
    tool_runtime = ToolRuntime()
    tool_runtime.register(adapter.descriptor, adapter.execute)

    result = await tool_runtime.call(
        context,
        "workflow.weekly-report.start",
        {"topic": "revenue"},
    )

    assert result.status is ToolResultStatus.STARTED
    assert result.run_id == "wf-run-1"
    assert adapter.local_durable_state_count == 0
    assert engine.started_requests[0].workflow_id == "weekly-report"
