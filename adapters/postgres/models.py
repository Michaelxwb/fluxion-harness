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

from adapters.postgres.base import Base, IdMixin, SoftDeleteTimestampMixin


class PlatformUserModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "platform_user"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_platform_user_ref: Mapped[str | None] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    __table_args__ = (Index("ix_platform_user_tenant_status", "tenant_id", "status"),)


class ChannelAccountModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "channel_account"

    channel: Mapped[str] = mapped_column(String(64), nullable=False)
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
            "channel",
            "account_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ChannelIdentityModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "channel_identity"

    channel_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_account.id"), nullable=False
    )
    peer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_channel_identity_active",
            "channel_account_id",
            "peer_type",
            "peer_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ChannelBindingModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "channel_binding"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    channel_identity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_identity.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_channel_binding_active_identity",
            "channel_identity_id",
            unique=True,
            postgresql_where=text("is_deleted = false AND status = 'active'"),
        ),
    )


class ExternalAuthProfileModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "external_auth_profile"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    provider_key: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(512))
    external_session_ref: Mapped[str | None] = mapped_column(String(512))
    external_session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    __table_args__ = (
        Index(
            "uq_external_auth_profile_active",
            "platform_user_id",
            "provider_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class UserMemoryModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "user_memory"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    __table_args__ = (Index("ix_user_memory_lookup", "tenant_id", "platform_user_id", "memory_type"),)


class ModelConfigModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "model_config"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_model_config_active_name", "name", unique=True, postgresql_where=text("is_deleted = false")
        ),
    )


class AgentDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "agent_definition"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_config_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("model_config.id"))
    memory_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_agent_definition_active_name",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ServiceDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "service_definition"

    service_key: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    current_release_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "service_release.id",
            use_alter=True,
            name="fk_service_definition_current_release_id_service_release",
        ),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_service_definition_active_key",
            "service_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ServiceReleaseModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "service_release"

    service_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id"), nullable=False
    )
    release_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    published_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_service_release_release_id",
            "service_id",
            "release_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "uq_service_release_hash",
            "service_id",
            "content_hash",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class SkillArtifactModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "skill_artifact"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    package_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="private")

    __table_args__ = (
        Index("ix_skill_artifact_name", "name"),
        Index("ix_skill_artifact_checksum", "checksum"),
    )


class KnowledgeSourceModel(Base, IdMixin, SoftDeleteTimestampMixin):
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
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class CapabilityDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "capability_definition"

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    side_effect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    auth_requirement: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_semantics: Mapped[str] = mapped_column(String(64), nullable=False, default="none")
    execution_characteristic: Mapped[str] = mapped_column(String(64), nullable=False, default="sync")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_capability_definition_active_name",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class CapabilityImplementationModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "capability_implementation"

    capability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("capability_definition.id"), nullable=False
    )
    impl_type: Mapped[str] = mapped_column(String(64), nullable=False)
    config_ref: Mapped[str | None] = mapped_column(String(1024))
    adapter: Mapped[str] = mapped_column(String(256), nullable=False)
    timeout_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    retry_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (Index("ix_capability_impl_lookup", "capability_id", "enabled", "is_deleted"),)


class IntegrationRegistrationModel(Base, SoftDeleteTimestampMixin):
    __tablename__ = "integration_registration"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    package_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    integration_type: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "workspace"

    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(64), nullable=False)
    root_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_workspace_owner", "tenant_id", "owner_type", "owner_id"),
        Index("ix_workspace_expiry", "status", "expires_at"),
    )


class UserServiceAuthModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "user_service_auth"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    service_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id"), nullable=False
    )
    resource_scope_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="*")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_user_service_auth_active",
            "platform_user_id",
            "service_id",
            "resource_scope_ref",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentSkillBindingModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "agent_skill_binding"
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    skill_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_artifact.id"), nullable=False
    )
    __table_args__ = (
        Index(
            "uq_agent_skill_binding_active",
            "agent_id",
            "skill_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentKnowledgeBindingModel(Base, IdMixin, SoftDeleteTimestampMixin):
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
            "agent_id",
            "knowledge_source_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentCapabilityBindingModel(Base, IdMixin, SoftDeleteTimestampMixin):
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
            "agent_id",
            "capability_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentServiceBindingModel(Base, IdMixin, SoftDeleteTimestampMixin):
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
            "agent_id",
            "service_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ConversationModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "conversation"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    __table_args__ = (Index("ix_conversation_user_time", "platform_user_id", "create_time"),)


class MessageModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "message"

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversation.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content_ref: Mapped[str | None] = mapped_column(String(1024))
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    channel_message_ref: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (Index("ix_message_conversation_time", "conversation_id", "create_time"),)


class ChannelDeliveryRouteModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "channel_delivery_route"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversation.id"))
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(256), nullable=False)
    peer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    __table_args__ = (Index("ix_delivery_route_user_conv", "platform_user_id", "conversation_id"),)


class ExecutionSnapshotModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "execution_snapshot"

    service_release_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    service_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_release_ref: Mapped[str | None] = mapped_column(String(256))
    agent_revision: Mapped[int | None] = mapped_column(Integer)
    snapshot_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ServiceExecutionModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "service_execution"

    service_release_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_release.id")
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
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

    __table_args__ = (
        Index(
            "uq_service_execution_active_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_service_execution_due", "status", "next_run_at"),
        Index("ix_service_execution_lease", "status", "lease_expires_at"),
        Index("ix_service_execution_actor_time", "actor_user_id", "create_time"),
        Index("ix_service_execution_trace", "trace_id"),
    )


class ExecutionStepModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "execution_step"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(256), nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_task_id: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str | None] = mapped_column(String(512))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_execution_step_execution_status", "execution_id", "status"),
        Index(
            "uq_execution_step_attempt_active",
            "execution_id",
            "step_key",
            "attempt",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ExecutionCommandModel(Base, IdMixin, SoftDeleteTimestampMixin):
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
            "idempotency_key",
            unique=True,
            postgresql_where=text("is_deleted = false AND idempotency_key IS NOT NULL"),
        ),
    )


class TaskProgressEventModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "task_progress_event"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(256))
    progress: Mapped[int | None] = mapped_column(Integer)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_progress_event_execution_time", "execution_id", "occurred_at"),)


class ArtifactModel(Base, IdMixin, SoftDeleteTimestampMixin):
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


class AuditLogModel(Base, IdMixin, SoftDeleteTimestampMixin):
    __tablename__ = "audit_log"

    tenant_id: Mapped[str | None] = mapped_column(String(128))
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(256), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    before_ref: Mapped[str | None] = mapped_column(String(1024))
    after_ref: Mapped[str | None] = mapped_column(String(1024))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_audit_log_entity_time", "entity_type", "entity_id", "create_time"),
        Index("ix_audit_log_actor_time", "actor_user_id", "create_time"),
    )
