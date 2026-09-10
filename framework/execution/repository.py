from typing import Protocol
from uuid import UUID

from framework.domain.execution import ExecutionSnapshot, ServiceExecution


class ExecutionRepository(Protocol):
    async def find_by_idempotency_key(self, key: str) -> ServiceExecution | None: ...
    async def create(self, execution: ServiceExecution, snapshot: ExecutionSnapshot) -> None: ...
    async def get(self, execution_id: UUID) -> ServiceExecution | None: ...
