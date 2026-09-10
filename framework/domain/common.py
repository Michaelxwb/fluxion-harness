from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PublishStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class EntityId(BaseModel):
    id: UUID = Field(default_factory=uuid4)
