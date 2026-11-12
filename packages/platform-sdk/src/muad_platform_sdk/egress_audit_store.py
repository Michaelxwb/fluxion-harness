"""Shared Runtime/Worker writer for the existing runtime.egress_audit contract."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .egress_attempt_audit import EgressAttempt

_INSERT = sa.text("""
INSERT INTO runtime.egress_audit
  (tenant_id, run_id, task_id, user_id, target_type, target, operation, method,
   policy_decision, status_code, result_status, latency_ms, error_code, attempt_id, phase)
VALUES
  (:tenant, :run, :task, :user, 'HTTP', :target, :operation, :method,
   :decision, :status, :result, :latency, :error, :attempt, 'completed')
ON CONFLICT (tenant_id, attempt_id, phase) DO NOTHING
""")


@dataclass(frozen=True, slots=True)
class EgressAuditIdentity:
    tenant_id: str
    user_id: uuid.UUID
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or (self.run_id is None and self.task_id is None):
            raise ValueError("trusted tenant and execution audit identity required")


class DatabaseAttemptObserver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], identity: EgressAuditIdentity,
                 *, timeout_seconds: float) -> None:
        self._sessions, self._identity, self._timeout = sessions, identity, timeout_seconds

    async def __call__(self, event: EgressAttempt) -> None:
        identity = self._identity
        decision = "ALLOW" if event.sent else ("DENY" if event.result_status == "BLOCKED" else "ERROR")
        async with asyncio.timeout(self._timeout):
            async with self._sessions.begin() as session:
                await session.execute(_INSERT, {
                    "tenant": identity.tenant_id, "run": identity.run_id, "task": identity.task_id,
                    "user": identity.user_id, "target": event.origin, "operation": event.route_id,
                    "method": event.method, "decision": decision, "status": event.status_code,
                    "result": event.result_status, "latency": event.latency_ms, "error": event.error_code,
                    "attempt": event.attempt_id,
                })
