from uuid import UUID

from pydantic import BaseModel, Field


class AgentDefinition(BaseModel):
    """V1.7 D04: Agent is direct-effect + revision, no Draft/Published lifecycle."""
    id: UUID
    name: str
    description: str = ""
    instructions: str
    model_config_ref: str
    skill_bindings: list[str] = Field(default_factory=list)
    knowledge_bindings: list[str] = Field(default_factory=list)
    capability_bindings: list[str] = Field(default_factory=list)
    service_bindings: list[str] = Field(default_factory=list)
    memory_policy: dict[str, object] = Field(default_factory=dict)
    revision: int = 1
    enabled: bool = True
