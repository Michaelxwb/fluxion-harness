from uuid import UUID

from pydantic import BaseModel, Field


class TrustedExecutionContext(BaseModel):
    """V1.7 D01: trusted system context only; unvalidated business scope
    travels on the proposal, never here."""

    actor_user_id: UUID
    tenant_id: str
    effective_capability_set: set[str] = Field(default_factory=set)
    auth_context_ref: str | None = None
    delivery_route_id: UUID | None = None
    workspace_id: UUID | None = None
