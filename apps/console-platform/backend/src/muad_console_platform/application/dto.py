import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=256)


class AccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["ADMIN", "BUILDER"] = "BUILDER"


class ConsoleAccountInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    role: str


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    instructions: str = Field(min_length=1)
    model_id: uuid.UUID
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    instructions: str | None = Field(default=None, min_length=1)
    model_id: uuid.UUID | None = None
    runtime_config: dict[str, Any] | None = None
    enabled: bool | None = None
    expected_revision: int = Field(ge=1)


class AgentListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    description: str | None
    model_id: uuid.UUID
    enabled: bool
    revision: int
    update_time: datetime


class AgentDetail(AgentListItem):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    instructions: str
    runtime_config: dict[str, Any]
    create_time: datetime


class SkillUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    platform_label: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None


class SkillUserScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_scope: Literal["ALL", "SELECTED"]


class SkillArtifactDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    artifact_id: uuid.UUID
    skill_id: uuid.UUID
    version: str
    checksum: str
    storage_key: str
    execution_mode: str
    default_script: str | None
    package_size: int
    validation_status: str
    frontmatter: dict[str, Any]
    manifest: dict[str, Any]
    created_by: uuid.UUID
    create_time: datetime


class SkillListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    key: str
    name: str
    description: str
    platform_label: str | None
    user_scope: str
    enabled: bool
    current_artifact_id: uuid.UUID | None
    current_version: str | None
    execution_mode: str | None
    update_time: datetime


class SkillDetail(SkillListItem):
    model_config = ConfigDict(extra="forbid")

    create_time: datetime
    current_artifact: SkillArtifactDetail | None
    agent_count: int
    user_count: int


class SkillUserGrantItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    user_code: str
    display_name: str
    granted_by: uuid.UUID
    create_time: datetime


class AgentSkillBindingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: uuid.UUID
    key: str
    name: str
    description: str
    platform_label: str | None
    user_scope: str
    enabled: bool
    current_version: str | None
    execution_mode: str | None
    sort_order: int
    bound_at: datetime


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_code: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|DISABLED)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    status: str | None = Field(default=None, pattern="^(ACTIVE|DISABLED)$")
    metadata: dict[str, Any] | None = None


class UserListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    user_code: str
    display_name: str
    status: str
    agent_grant_count: int
    credential_count: int
    identity_count: int
    memory_count: int
    create_time: datetime
    update_time: datetime


class UserDetail(UserListItem):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    metadata: dict[str, Any]


class AgentGrantItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    agent_key: str
    agent_name: str
    enabled: bool
    granted_at: datetime
    granted_by: uuid.UUID


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    memory_key: str
    category: str
    content: dict[str, Any]
    source_type: str
    source_ref: str | None
    version: int
    enabled: bool
    create_time: datetime
    update_time: datetime


class BindCodeCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bind_code: str
    expires_at: datetime
    status: str


class IdentityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    channel: str
    external_user_id: str
    bot_id: str
    bound_at: datetime
    last_active_at: datetime | None
    user_status: str


class ModelCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    protocol: Literal["OPENAI"] = "OPENAI"
    base_url: str = Field(min_length=1)
    model_id: str = Field(min_length=1, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value


class ModelUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1)
    api_key: str | None = Field(default=None, max_length=512)
    params: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _require_http_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value


class ModelListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    key: str
    name: str
    protocol: str
    model_id: str
    base_url: str
    api_key_configured: bool
    params: dict[str, Any]
    revision: int
    enabled: bool
    last_test_status: str
    last_test_at: datetime | None
    create_time: datetime
    update_time: datetime


class ModelBatchTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
