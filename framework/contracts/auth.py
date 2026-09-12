from typing import Protocol

from pydantic import BaseModel, Field

from framework.contracts.context import TrustedExecutionContext


class InvocationCredential(BaseModel):
    kind: str
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class AuthProvider(Protocol):
    async def get_invocation_credential(
        self,
        *,
        context: TrustedExecutionContext,
    ) -> InvocationCredential: ...
