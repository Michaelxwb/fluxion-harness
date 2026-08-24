from __future__ import annotations

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.runtime.tools import ToolAuthorizationError, ToolDescriptor, ToolRuntime


@pytest.mark.asyncio
async def test_E_R03_tool_runtime_rejects_when_agent_allowlist_excludes_user_grant() -> None:
    context, _runtime = await runtime_context()
    tool_runtime = ToolRuntime()
    tool_runtime.register(
        ToolDescriptor(tool_id="danger.delete", capability_id="cap.delete", name="delete"),
        lambda _ctx, _args: {"deleted": True},
    )

    with pytest.raises(ToolAuthorizationError) as exc_info:
        await tool_runtime.call(
            context,
            "danger.delete",
            {},
            user_grants={"danger.delete"},
            agent_allowlist={"safe.read"},
            tenant_policy={"danger.delete"},
        )

    assert exc_info.value.code == "tool_not_allowed"
    assert any(event.name == "tool.policy_decision" for event in context.trace)
    decision = next(event for event in context.trace if event.name == "tool.policy_decision")
    assert decision.attributes["allowed"] is False
    assert decision.attributes["tool_id"] == "danger.delete"
