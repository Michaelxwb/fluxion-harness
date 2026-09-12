from typing import Protocol

from pydantic import BaseModel, Field

from framework.contracts.context import TrustedExecutionContext


class KnowledgeHit(BaseModel):
    source_id: str
    document_ref: str
    title: str | None = None
    content: str
    score: float | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class KnowledgeProvider(Protocol):
    async def search(
        self,
        *,
        query: str,
        source_ids: list[str],
        context: TrustedExecutionContext,
    ) -> list[KnowledgeHit]: ...
