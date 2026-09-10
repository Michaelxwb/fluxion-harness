from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from framework.contracts.context import TrustedExecutionContext


class SandboxResult(BaseModel):
    data: dict[str, object] = Field(default_factory=dict)


class SandboxExecutor(Protocol):
    async def execute(
        self,
        *,
        workspace_id: UUID,
        operation: str,
        arguments: dict[str, object],
        context: TrustedExecutionContext,
    ) -> SandboxResult: ...
