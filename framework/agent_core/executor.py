from typing import Protocol
from pydantic import BaseModel, Field

from framework.contracts.context import TrustedExecutionContext


class AgentInput(BaseModel):
    agent_id: str
    message: str
    conversation_id: str | None = None


class AgentResult(BaseModel):
    content: str
    proposed_actions: list[dict[str, object]] = Field(default_factory=list)


class AgentExecutor(Protocol):
    async def invoke(self, *, context: TrustedExecutionContext, input: AgentInput) -> AgentResult: ...
