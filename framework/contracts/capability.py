from typing import Protocol
from pydantic import BaseModel, Field

from framework.contracts.context import TrustedExecutionContext


class CapabilityContract(BaseModel):
    name: str
    description: str
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    side_effect: bool = False
    risk_level: str = "low"
    idempotency_semantics: str = "none"
    execution_characteristic: str = "sync"


class CapabilityResult(BaseModel):
    data: object | None = None
    external_task_ref: str | None = None


class CapabilityProvider(Protocol):
    async def invoke(
        self,
        *,
        contract: CapabilityContract,
        input: dict[str, object],
        context: TrustedExecutionContext,
    ) -> CapabilityResult: ...
