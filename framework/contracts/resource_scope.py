from pydantic import BaseModel, Field

# Scope error taxonomy — split by *who is at fault*, not by where it is raised.
#
# Authoring side (manifest / publish input; the builder can fix it):
SERVICE_SCOPE_SCHEMA_INVALID = "SERVICE_SCOPE_SCHEMA_INVALID"  # manifest declared a malformed JSON Schema
SERVICE_SCOPE_TYPE_UNKNOWN = (
    "SERVICE_SCOPE_TYPE_UNKNOWN"  # Service declares a type no loaded manifest declares
)
#
# Runtime side (the framework's own frozen state is inconsistent with the registry;
# the caller cannot fix it):
SERVICE_CONFIGURATION_INVALID = (
    "SERVICE_CONFIGURATION_INVALID"  # Release froze a type absent from the registry
)
SCOPE_INVALID = "SCOPE_INVALID"  # the proposed scope value violates the frozen schema
#
# Reserved for when framework-side authorization for a scope is introduced (REQ-EXEC-001);
# not raised anywhere yet.
SCOPE_FORBIDDEN = "SCOPE_FORBIDDEN"


class ResourceScopeType(BaseModel):
    """A scope type declared by an Integration manifest (V1.7 D01)."""

    name: str
    schema_: dict[str, object] = Field(alias="schema")
    schema_hash: str


class ValidatedResourceScope(BaseModel):
    scope_type: str
    schema_hash: str
    value: dict[str, object] = Field(default_factory=dict)
