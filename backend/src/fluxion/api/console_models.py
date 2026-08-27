from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from fluxion.resources import ResourceKind, ResourceVisibility


class ResourceCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = None
    resource_id: str
    version: str
    spec: dict[str, object]
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE


class ResourceUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, object]


class PublishPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_note: str | None = None
    expected_base_version: str | None = None


class RollbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_version: str
    force: bool = False
    approval_id: str | None = None


class DeprecatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class BindingCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: str
    subject_id: str
    resource_type: ResourceKind
    resource_id: str
    version_selector: str = "latest-published"
    credential_ref: str | None = None
    config: dict[str, object] = Field(default_factory=dict)


class WorkflowValidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlatformUserCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_user_id: str
    display_name: str = ""


class ChatAccessCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str


class ApprovalCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: str
    resource_id: str
    target_version: str
    reason: str | None = None
    ttl_seconds: float = 3600.0


class ApprovalDecidePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str | None = None
