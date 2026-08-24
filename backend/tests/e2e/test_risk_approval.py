from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from fluxion.services.approval import (
    ApprovalDeniedError,
    ApprovalOutcome,
    ApprovalRequest,
    InMemoryApprovalAuditSink,
    InMemoryApprovalDecisionStore,
    RiskApprovalGate,
    RiskLevel,
)


class StubApprovalProvider:
    def __init__(self, mode: str = "approved") -> None:
        self.mode = mode
        self.calls = 0

    async def decide(self, request: ApprovalRequest) -> ApprovalOutcome:
        del request
        self.calls += 1
        if self.mode == "timeout":
            await asyncio.sleep(0.05)
        if self.mode == "unavailable":
            raise ConnectionError("approver unavailable")
        if self.mode == "rejected":
            return ApprovalOutcome.REJECTED
        return ApprovalOutcome.APPROVED


@pytest.mark.asyncio
async def test_S_C116_low_medium_high_risk_policies_gate_execution() -> None:
    provider = StubApprovalProvider()
    audit = InMemoryApprovalAuditSink()
    gate = RiskApprovalGate(
        provider,
        InMemoryApprovalDecisionStore(),
        audit,
        timeout_seconds=0.01,
    )
    executions: list[str] = []

    async def execute(action: str) -> str:
        executions.append(action)
        return action

    low = await gate.execute(
        _request("req-low", RiskLevel.LOW),
        _operation(execute, "low"),
    )
    with pytest.raises(ApprovalDeniedError, match="明确确认"):
        await gate.execute(
            _request("req-medium-unconfirmed", RiskLevel.MEDIUM),
            _operation(execute, "medium-unconfirmed"),
        )
    medium = await gate.execute(
        _request("req-medium", RiskLevel.MEDIUM, confirmed=True),
        _operation(execute, "medium"),
    )
    high = await gate.execute(
        _request("req-high", RiskLevel.HIGH),
        _operation(execute, "high"),
    )

    assert (low, medium, high) == ("low", "medium", "high")
    assert executions == ["low", "medium", "high"]
    assert provider.calls == 1
    assert [record.outcome for record in audit.records] == [
        ApprovalOutcome.AUTO_APPROVED,
        ApprovalOutcome.CONFIRMATION_REQUIRED,
        ApprovalOutcome.APPROVED,
        ApprovalOutcome.APPROVED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "rejected", "unavailable"])
async def test_E_C113_high_risk_failure_is_cached_and_fails_closed(mode: str) -> None:
    provider = StubApprovalProvider(mode)
    decisions = InMemoryApprovalDecisionStore()
    audit = InMemoryApprovalAuditSink()
    gate = RiskApprovalGate(provider, decisions, audit, timeout_seconds=0.001)
    request = _request(f"req-{mode}", RiskLevel.HIGH)
    executions = 0

    async def execute() -> None:
        nonlocal executions
        executions += 1

    with pytest.raises(ApprovalDeniedError):
        await gate.execute(request, execute)
    provider.mode = "approved"
    with pytest.raises(ApprovalDeniedError):
        await gate.execute(request, execute)

    assert executions == 0
    assert provider.calls == 1
    assert len(audit.records) == 1
    assert audit.records[0].outcome in {
        ApprovalOutcome.TIMED_OUT,
        ApprovalOutcome.REJECTED,
        ApprovalOutcome.UNAVAILABLE,
    }


def _request(
    request_id: str,
    risk: RiskLevel,
    *,
    confirmed: bool = False,
) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=request_id,
        tenant_id="tenant-a",
        actor_id="admin-001",
        action="workflow.execute",
        target_id="workflow/weekly-report",
        risk=risk,
        explicit_confirmation=confirmed,
    )


def _operation(
    execute: Callable[[str], Awaitable[str]],
    action: str,
) -> Callable[[], Awaitable[str]]:
    async def operation() -> str:
        return await execute(action)

    return operation
