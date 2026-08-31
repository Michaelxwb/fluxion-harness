"""TASK-005：统一 PolicyDecisionService 决策链（version/schema/semantic/risk/approval）。"""

from __future__ import annotations

import pytest

from fluxion.runtime.tools import (
    PolicyDecision,
    PolicyDecisionService,
    ToolDescriptor,
    ToolRuntime,
    ValidatorRegistry,
)
from tests.runtime_helpers import minimal_tool_context

_STAGES = ["version", "schema", "semantic", "risk", "approval"]


@pytest.mark.asyncio
async def test_T005_decision_chain_has_five_stages() -> None:
    service = PolicyDecisionService(ValidatorRegistry())
    descriptor = ToolDescriptor(tool_id="t", capability_id="cap.x", name="t", risk_level="high")

    result = await service.decide(
        minimal_tool_context({}), descriptor, {}, allowed=True
    )

    assert result.decision is PolicyDecision.REQUIRE_APPROVAL
    assert [step.stage for step in result.chain] == _STAGES
    assert result.chain[-1].outcome == "required"
    assert result.schema_error is None
    assert result.semantic_denied is False


@pytest.mark.asyncio
async def test_T005_deny_captures_schema_error_in_chain() -> None:
    service = PolicyDecisionService(ValidatorRegistry())
    descriptor = ToolDescriptor(
        tool_id="search",
        capability_id="cap.search",
        name="search",
        parameters_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    result = await service.decide(minimal_tool_context({}), descriptor, {}, allowed=True)

    assert result.schema_error is not None
    assert result.chain[1].stage == "schema"
    assert result.chain[1].outcome == "deny"


@pytest.mark.asyncio
async def test_T005_policy_decision_event_carries_audit_chain() -> None:
    runtime = ToolRuntime()
    runtime.register(
        ToolDescriptor(tool_id="t", capability_id="cap.x", name="t"),
        lambda ctx, args: {"ok": True},
    )
    context = minimal_tool_context(
        {"user_tools": ["t"], "agent_tools": ["t"], "tenant_tools": ["t"]}
    )

    await runtime.call(context, "t", {})

    decision = next(e for e in context.trace if e.name == "tool.policy_decision")
    chain = decision.attributes["chain"]
    assert [step["stage"] for step in chain] == _STAGES
    assert decision.attributes["decision"] == "allow"
