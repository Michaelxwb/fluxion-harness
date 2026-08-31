from __future__ import annotations

import pytest
from tests.runtime_helpers import minimal_tool_context

from fluxion.runtime.tools import ToolDescriptor, ToolResult, ToolResultStatus, ToolRuntime
from fluxion.runtime.workflow import WorkflowAdapter
from tests.fakes.workflow import StubWorkflowEngine


@pytest.mark.asyncio
async def test_S_R14_sync_workflow_and_streaming_tools_return_unified_result_envelopes() -> None:
    granted = {"calc.add", "workflow.invoice.start", "mcp.stream"}
    context = minimal_tool_context(
        {
            "user_tools": sorted(granted),
            "agent_tools": sorted(granted),
            "tenant_tools": sorted(granted),
        }
    )
    tool_runtime = ToolRuntime()
    workflow = WorkflowAdapter(
        workflow_id="invoice",
        engine=StubWorkflowEngine(run_id="wf-invoice-1"),
    )
    tool_runtime.register(
        ToolDescriptor(tool_id="calc.add", capability_id="cap.calc", name="calc.add"),
        lambda _ctx, _args: {"value": 3},
    )
    tool_runtime.register(workflow.descriptor, workflow.execute)
    tool_runtime.register(
        ToolDescriptor(tool_id="mcp.stream", capability_id="cap.stream", name="mcp.stream"),
        lambda _ctx, _args: ToolResult.streamed([{"delta": "a"}, {"delta": "b"}]),
    )

    completed = await tool_runtime.call(context, "calc.add", {})
    started = await tool_runtime.call(context, "workflow.invoice.start", {})
    streamed = await tool_runtime.call(context, "mcp.stream", {})

    assert completed.status is ToolResultStatus.COMPLETED
    assert completed.result == {"value": 3}
    assert started.status is ToolResultStatus.STARTED
    assert started.run_id == "wf-invoice-1"
    assert streamed.status is ToolResultStatus.STREAMED
    assert streamed.events == [{"delta": "a"}, {"delta": "b"}]
    assert completed.policy_decision_id
    assert started.policy_decision_id
    assert streamed.policy_decision_id
    assert [event.name for event in context.trace].count("tool.policy_decision") == 3
