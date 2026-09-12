from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin


class PlatformUserModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "platform_user"

    user_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="END_USER")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")

    __table_args__ = (
        Index(
            "uq_platform_user_active_key",
            "tenant_id",
            "user_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AuthAccountModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """Console login account (ADR-021). Passwords are PBKDF2 hashes only."""

    __tablename__ = "auth_account"

    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="BUILDER")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_auth_account_active_username",
            "tenant_id",
            "username",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AuthSessionModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """Bearer session token store; only the SHA-256 hash of the token persists."""

    __tablename__ = "auth_session"

    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("auth_account.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_auth_session_token", "token_hash", unique=True),
        Index("ix_auth_session_account", "account_id", "expires_at"),
    )


class ProjectPlatformModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "project_platform"

    key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    auth_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_project_platform_active_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class UserProjectCredentialModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "user_project_credential"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    project_platform_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("project_platform.id"), nullable=False
    )
    credential_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNVERIFIED")

    __table_args__ = (
        Index(
            "uq_user_project_credential_active",
            "tenant_id",
            "platform_user_id",
            "project_platform_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentAccessGrantModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """The single authorization fact: platform_user → agent (RULE-USER-01)."""

    __tablename__ = "agent_access_grant"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    granted_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_agent_access_grant_active",
            "tenant_id",
            "platform_user_id",
            "agent_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ChannelAccountModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "channel_account"

    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    account_key: Mapped[str] = mapped_column(String(256), nullable=False)
    default_agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id")
    )
    config_ref: Mapped[str | None] = mapped_column(String(512))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_channel_account_active",
            "tenant_id",
            "channel_type",
            "account_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ChannelIdentityModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "channel_identity"

    channel_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_account.id"), nullable=False
    )
    identity_scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_identity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_channel_identity_active",
            "tenant_id",
            "channel_account_id",
            "identity_scope_key",
            "external_user_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ChannelBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "channel_binding"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    channel_identity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_identity.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_channel_binding_active_identity",
            "channel_identity_id",
            unique=True,
            postgresql_where=text("is_deleted = false AND status = 'ACTIVE'"),
        ),
    )


class BindCodeModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """One-shot /bind code; only the hash is stored, the plaintext is shown once."""

    __tablename__ = "bind_code"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    used_identity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_identity.id")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_bind_code_hash", "tenant_id", "code_hash", unique=True),
        Index(
            "uq_bind_code_single_pending",
            "tenant_id",
            "platform_user_id",
            "channel_type",
            unique=True,
            postgresql_where=text("is_deleted = false AND status = 'PENDING'"),
        ),
    )


class UserMemoryModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "user_memory"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    memory_key: Mapped[str] = mapped_column(String(256), nullable=False)
    memory_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(512))
    confidence: Mapped[float] = mapped_column(nullable=False, default=1.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")

    __table_args__ = (
        Index(
            "uq_user_memory_active_key",
            "tenant_id",
            "platform_user_id",
            "memory_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ModelConfigModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "model_config"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_model_config_active_name",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_definition"

    agent_key: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_config_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("model_config.id"))
    memory_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_agent_definition_active_key",
            "tenant_id",
            "agent_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ServiceDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "service_definition"

    service_key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id")
    )
    execution_type: Mapped[str] = mapped_column(String(32), nullable=False, default="HYBRID")
    draft_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    current_release_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "service_release.id",
            use_alter=True,
            name="fk_service_definition_current_release_id_service_release",
        ),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_service_definition_active_key",
            "tenant_id",
            "service_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ServiceReleaseModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "service_release"

    service_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id"), nullable=False
    )
    release_no: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    published_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_service_release_no",
            "tenant_id",
            "service_id",
            "release_no",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "uq_service_release_hash",
            "tenant_id",
            "service_id",
            "content_hash",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class SkillDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "skill_definition"

    skill_key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latest_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "skill_artifact.id",
            use_alter=True,
            name="fk_skill_definition_latest_artifact_id_skill_artifact",
        ),
    )
    platform_label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_skill_definition_active_key",
            "tenant_id",
            "skill_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class SkillArtifactModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "skill_artifact"

    skill_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_definition.id")
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    artifact_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    sdk_version: Mapped[str | None] = mapped_column(String(64))
    entrypoint: Mapped[str | None] = mapped_column(String(256))
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")

    __table_args__ = (
        Index(
            "uq_skill_artifact_checksum",
            "tenant_id",
            "skill_id",
            "checksum",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_skill_artifact_name", "name"),
    )


class KnowledgeSourceModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "knowledge_source"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(128), nullable=False)
    config_ref: Mapped[str | None] = mapped_column(String(1024))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_knowledge_source_active_name",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class CapabilityDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "capability_definition"

    capability_key: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    side_effect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="LOW")
    auth_requirement: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_semantics: Mapped[str] = mapped_column(String(64), nullable=False, default="NONE")
    execution_characteristic: Mapped[str] = mapped_column(String(64), nullable=False, default="SYNC")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_capability_definition_active_key",
            "tenant_id",
            "capability_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class CapabilityImplementationModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "capability_implementation"

    capability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("capability_definition.id"), nullable=False
    )
    implementation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    config_ref: Mapped[str | None] = mapped_column(String(1024))
    adapter: Mapped[str] = mapped_column(String(256), nullable=False)
    execution_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    data_retrieval_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (Index("ix_capability_impl_lookup", "capability_id", "enabled", "is_deleted"),)


class WorkspaceModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "workspace"

    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(64), nullable=False)
    root_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_workspace_active_owner",
            "tenant_id",
            "owner_type",
            "owner_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_workspace_expiry", "status", "expires_at"),
    )


class AgentSkillBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_skill_binding"
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    skill_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_definition.id"), nullable=False
    )
    __table_args__ = (
        Index(
            "uq_agent_skill_binding_active",
            "tenant_id",
            "agent_id",
            "skill_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentKnowledgeBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_knowledge_binding"
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    knowledge_source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("knowledge_source.id"), nullable=False
    )
    __table_args__ = (
        Index(
            "uq_agent_knowledge_binding_active",
            "tenant_id",
            "agent_id",
            "knowledge_source_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentCapabilityBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_capability_binding"
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    capability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("capability_definition.id"), nullable=False
    )
    __table_args__ = (
        Index(
            "uq_agent_capability_binding_active",
            "tenant_id",
            "agent_id",
            "capability_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentServiceBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_service_binding"
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    service_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id"), nullable=False
    )
    __table_args__ = (
        Index(
            "uq_agent_service_binding_active",
            "tenant_id",
            "agent_id",
            "service_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ConversationModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "conversation"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("agent_definition.id"))
    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")

    __table_args__ = (
        Index("ix_conversation_user_time", "platform_user_id", "create_time"),
        Index("ix_conversation_agent", "agent_id", "status"),
    )


class MessageModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "message"

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversation.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content_ref: Mapped[str | None] = mapped_column(String(1024))
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    external_message_id: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        Index("ix_message_conversation_time", "conversation_id", "create_time"),
        Index("uq_message_external", "tenant_id", "external_message_id", unique=True),
    )


class ChannelDeliveryRouteModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "channel_delivery_route"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversation.id"))
    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(256), nullable=False)
    peer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")

    __table_args__ = (
        Index("ix_delivery_route_user_conv", "platform_user_id", "conversation_id"),
        Index(
            "uq_channel_delivery_route_active",
            "tenant_id",
            "platform_user_id",
            "channel_type",
            unique=True,
            postgresql_where=text("is_deleted = false AND status = 'ACTIVE'"),
        ),
    )


class ChannelDeliveryModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """Outbound delivery attempt facts. Retry owner is the Worker (V1.7 D03)."""

    __tablename__ = "channel_delivery"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    step_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("execution_step.id"))
    delivery_route_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_delivery_route.id"), nullable=False
    )
    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_channel_delivery_dedupe", "tenant_id", "dedupe_key", unique=True),
        Index("ix_channel_delivery_execution", "execution_id", "status"),
    )


class ExecutionSnapshotModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "execution_snapshot"

    service_release_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_release.id")
    )
    service_release_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    service_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_release_ref: Mapped[str | None] = mapped_column(String(256))
    agent_revision: Mapped[int | None] = mapped_column(Integer)
    snapshot_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index(
            "uq_execution_snapshot_hash",
            "tenant_id",
            "service_release_id",
            "service_content_hash",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ServiceExecutionModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "service_execution"

    service_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id")
    )
    service_release_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_release.id")
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    primary_agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id")
    )
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversation.id"))
    delivery_route_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_delivery_route.id")
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_snapshot.id"), nullable=False
    )
    workspace_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workspace.id"))
    service_release_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    current_step: Mapped[str | None] = mapped_column(String(256))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(256))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128))
    channel_source: Mapped[str | None] = mapped_column(String(32))
    waiting_reason: Mapped[str | None] = mapped_column(String(256))
    human_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_action: Mapped[str | None] = mapped_column(String(16))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index(
            "uq_service_execution_active_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_service_execution_due", "status", "next_run_at"),
        Index("ix_service_execution_lease", "status", "lease_expires_at"),
        Index("ix_service_execution_actor_time", "actor_user_id", "create_time"),
        Index("ix_service_execution_trace", "trace_id"),
    )


class ExecutionStepModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "execution_step"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(256), nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(512))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_execution_step_execution_status", "execution_id", "status"),
        Index(
            "uq_execution_step_active",
            "tenant_id",
            "execution_id",
            "step_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AsyncTaskRunModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """One external async task per step (module 05: UNIQUE(tenant, execution_step_id))."""

    __tablename__ = "async_task_run"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    execution_step_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_step.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    external_task_id: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUBMITTED")
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    poll_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index(
            "uq_async_task_run_step",
            "tenant_id",
            "execution_step_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_async_task_run_poll", "status", "next_poll_at"),
    )


class ExecutionCommandModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "execution_command"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")

    __table_args__ = (
        Index("ix_execution_command_execution_status", "execution_id", "status"),
        Index(
            "uq_execution_command_active_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("is_deleted = false AND idempotency_key IS NOT NULL"),
        ),
    )


class TaskProgressEventModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "task_progress_event"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(256))
    progress: Mapped[int | None] = mapped_column(Integer)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="USER")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_progress_event_execution_time", "execution_id", "occurred_at"),)


class ArtifactModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "artifact"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    execution_step_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_step.id")
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_artifact_execution", "execution_id", "execution_step_id"),)


class AuditLogModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "audit_log"

    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    before_ref: Mapped[str | None] = mapped_column(String(1024))
    after_ref: Mapped[str | None] = mapped_column(String(1024))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_audit_log_entity_time", "resource_type", "resource_id", "create_time"),
        Index("ix_audit_log_actor_time", "actor_user_id", "create_time"),
    )
