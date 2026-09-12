from datetime import datetime
from typing import Protocol
from uuid import UUID

from framework.domain.execution import ExecutionSnapshot, ExecutionStatus, ServiceExecution


class ExecutionRepository(Protocol):
    async def find_by_idempotency_key(self, key: str) -> ServiceExecution | None: ...
    async def create(self, execution: ServiceExecution, snapshot: ExecutionSnapshot) -> None: ...
    async def get(self, execution_id: UUID) -> ServiceExecution | None: ...
    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[ServiceExecution]: ...
    async def heartbeat(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> None: ...
    async def mark_waiting(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        next_run_at: datetime,
        status: ExecutionStatus | None = None,
    ) -> None: ...
    async def mark_terminal(
        self,
        execution_id: UUID,
        *,
        worker_id: str,
        status: ExecutionStatus,
    ) -> None: ...
