from __future__ import annotations

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.runtime.tools import ToolDescriptor, ToolResult, ToolResultStatus, ToolRuntime
from fluxion.runtime.workflow import WorkflowAdapter
from tests.fakes.workflow import StubWorkflowEngine


@pytest.mark.asyncio
async def test_S_R14_sync_workflow_and_streaming_tools_return_unified_result_envelopes() -> None:
    context, _runtime = await runtime_context()
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

    common = {
        "user_grants": {"calc.add", "workflow.invoice.start", "mcp.stream"},
        "agent_allowlist": {"calc.add", "workflow.invoice.start", "mcp.stream"},
        "tenant_policy": {"calc.add", "workflow.invoice.start", "mcp.stream"},
    }
    completed = await tool_runtime.call(context, "calc.add", {}, **common)
    started = await tool_runtime.call(context, "workflow.invoice.start", {}, **common)
    streamed = await tool_runtime.call(context, "mcp.stream", {}, **common)

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
