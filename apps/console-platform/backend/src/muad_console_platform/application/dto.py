import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
