from uuid import UUID

from pydantic import BaseModel, Field

from framework.domain.common import PublishStatus


class ServiceDefinition(BaseModel):
    id: UUID
    service_key: str
    name: str
    goal: str
    input_schema: dict[str, object] = Field(default_factory=dict)
    execution_spec: dict[str, object] = Field(default_factory=dict)
    confirmation_rules: dict[str, object] = Field(default_factory=dict)
    deliverable_spec: dict[str, object] = Field(default_factory=dict)
    exception_policy: dict[str, object] = Field(default_factory=dict)
    status: PublishStatus = PublishStatus.DRAFT
    enabled: bool = True
