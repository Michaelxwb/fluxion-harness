from pydantic import BaseModel, Field

SCOPE_INVALID = "SCOPE_INVALID"
SCOPE_FORBIDDEN = "SCOPE_FORBIDDEN"
SERVICE_CONFIGURATION_INVALID = "SERVICE_CONFIGURATION_INVALID"


class ResourceScopeType(BaseModel):
    """A scope type declared by an Integration manifest (V1.7 D01)."""

    name: str
    schema_: dict[str, object] = Field(alias="schema")
    schema_hash: str


class ValidatedResourceScope(BaseModel):
    scope_type: str
    schema_hash: str
    value: dict[str, object] = Field(default_factory=dict)
