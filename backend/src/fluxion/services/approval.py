from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, TypeVar
from uuid import uuid4

from fluxion.registry import AuditRecord, RegistryStore


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalOutcome(StrEnum):
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    CONFIRMATION_REQUIRED = "confirmation_required"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_id: str
    tenant_id: str
    actor_id: str
    action: str
    target_id: str
    risk: RiskLevel
    explicit_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    request: ApprovalRequest
    outcome: ApprovalOutcome
    decided_at: datetime

    @property
    def allowed(self) -> bool:
        return self.outcome in {
            ApprovalOutcome.AUTO_APPROVED,
            ApprovalOutcome.APPROVED,
        }


class ApprovalProvider(Protocol):
    async def decide(self, request: ApprovalRequest) -> ApprovalOutcome: ...


class ApprovalDecisionStore(Protocol):
    async def get(self, request_id: str, *, tenant_id: str) -> ApprovalDecision | None: ...

    async def put(self, decision: ApprovalDecision) -> None: ...


class ApprovalAuditSink(Protocol):
    async def append(self, decision: ApprovalDecision) -> None: ...


class InMemoryApprovalDecisionStore:
    def __init__(self) -> None:
        self._decisions: dict[tuple[str, str], ApprovalDecision] = {}

    async def get(self, request_id: str, *, tenant_id: str) -> ApprovalDecision | None:
        return self._decisions.get((tenant_id, request_id))

    async def put(self, decision: ApprovalDecision) -> None:
        key = (decision.request.tenant_id, decision.request.request_id)
        self._decisions.setdefault(key, decision)


class InMemoryApprovalAuditSink:
    def __init__(self) -> None:
        self.records: list[ApprovalDecision] = []

    async def append(self, decision: ApprovalDecision) -> None:
        self.records.append(decision)


class RegistryApprovalAuditSink:
    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    async def append(self, decision: ApprovalDecision) -> None:
        request = decision.request
        await self._store.append_audit(
            AuditRecord(
                audit_id=f"audit_{uuid4().hex}",
                tenant_id=request.tenant_id,
                actor_id=request.actor_id,
                request_id=request.request_id,
                action="approval.decision",
                target_type=request.action,
                target_id=request.target_id,
                before=None,
                after={"risk": request.risk.value, "outcome": decision.outcome.value},
            )
        )


class ApprovalDeniedError(RuntimeError):
    pass


ResultT = TypeVar("ResultT")


class RiskApprovalGate:
    def __init__(
        self,
        provider: ApprovalProvider,
        decisions: ApprovalDecisionStore,
        audit: ApprovalAuditSink,
        *,
        timeout_seconds: float,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._provider = provider
        self._decisions = decisions
        self._audit = audit
        self._timeout_seconds = timeout_seconds

    async def execute(
        self,
        request: ApprovalRequest,
        operation: Callable[[], Awaitable[ResultT]],
    ) -> ResultT:
        decision = await self.authorize(request)
        if not decision.allowed:
            raise ApprovalDeniedError(_denial_message(decision.outcome))
        return await operation()

    async def authorize(self, request: ApprovalRequest) -> ApprovalDecision:
        existing = await self._decisions.get(
            request.request_id,
            tenant_id=request.tenant_id,
        )
        if existing is not None:
            return existing
        outcome = await self._decide(request)
        decision = ApprovalDecision(request, outcome, datetime.now(UTC))
        # Audit 失败时不保存允许结果，也不会进入受保护操作。
        await self._audit.append(decision)
        await self._decisions.put(decision)
        return decision

    async def _decide(self, request: ApprovalRequest) -> ApprovalOutcome:
        if request.risk is RiskLevel.LOW:
            return ApprovalOutcome.AUTO_APPROVED
        if request.risk is RiskLevel.MEDIUM:
            return (
                ApprovalOutcome.APPROVED
                if request.explicit_confirmation
                else ApprovalOutcome.CONFIRMATION_REQUIRED
            )
        try:
            return await asyncio.wait_for(
                self._provider.decide(request),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            return ApprovalOutcome.TIMED_OUT
        except Exception:  # noqa: BLE001 - provider failure maps to fail-closed outcome
            return ApprovalOutcome.UNAVAILABLE


def _denial_message(outcome: ApprovalOutcome) -> str:
    if outcome is ApprovalOutcome.CONFIRMATION_REQUIRED:
        return "medium risk 操作需要明确确认"
    return f"审批未通过: {outcome.value}"
