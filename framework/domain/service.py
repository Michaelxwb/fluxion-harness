from uuid import UUID

from pydantic import BaseModel, Field


class ServiceDefinition(BaseModel):
    """Domain view of a Service definition (module 05 §3.3 `service_definition`).

    Field names match the persisted design columns: `key` is the stable
    identifier and `description` is the business description. The publish state
    is the `current_release_id` pointer — the design deliberately has **no**
    `status` column (the Console derives `draft_state` from the pointer plus the
    draft revision, see FE-02 Z-07), so this model must not invent one.
    """

    id: UUID
    key: str
    name: str
    description: str
    primary_agent_id: UUID | None = None
    execution_type: str = "HYBRID"
    draft_payload: dict[str, object] = Field(default_factory=dict)
    draft_revision: int = 1
    current_release_id: UUID | None = None
    enabled: bool = True
    created_by: UUID | None = None
