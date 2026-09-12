from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EntityId(BaseModel):
    """Identity mixin for domain objects.

    Deliberately minimal: the design has no shared `status` concept — publish
    state is a pointer (`current_release_id`), execution state is an explicit
    nine-value lifecycle, and draft state is derived — so no generic status enum
    is defined here (inventing one is how the skeleton drifted from the design).
    """

    id: UUID = Field(default_factory=uuid4)
