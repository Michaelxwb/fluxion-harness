from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin


class PlatformUserModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "platform_user"

    user_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="END_USER")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

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
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

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
    external_session_ref: Mapped[str | None] = mapped_column(String(1024))
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    refresh_owner: Mapped[str | None] = mapped_column(String(256))
    refresh_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
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
    agent_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    granted_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_agent_access_grant_active",
            "tenant_id",
            "platform_user_id",
            "agent_definition_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ChannelAccountModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "channel_account"

    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    account_key: Mapped[str] = mapped_column(String(256), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    connection_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_scope_key: Mapped[str] = mapped_column(String(256), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_identity_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index(
            "uq_channel_identity_active",
            "tenant_id",
            "channel_type",
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
    binding_method: Mapped[str] = mapped_column(String(64), nullable=False, default="BIND_CODE")
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
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
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id")
    )
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
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
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
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="OPENAI_COMPATIBLE")
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    api_key_secret_ref: Mapped[str | None] = mapped_column(String(512))
    default_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    extra_headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
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

    key: Mapped[str] = mapped_column(String(160), nullable=False, default="")
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
            "key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ServiceDefinitionModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "service_definition"

    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_agent_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id")
    )
    execution_type: Mapped[str] = mapped_column(String(32), nullable=False, default="HYBRID")
    draft_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    draft_revision: Mapped[int | None] = mapped_column(BigInteger)
    current_release_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "service_release.id",
            use_alter=True,
            name="fk_service_definition_current_release_id_service_release",
        ),
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_service_definition_active_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_service_definition_created_by", "tenant_id", "created_by"),
    )


class ServiceReleaseModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "service_release"

    service_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # deferrable: service_definition.current_release_id -> service_release is a
        # cycle, so this side of the pair is added by ALTER after both tables exist.
        ForeignKey(
            "service_definition.id",
            use_alter=True,
            name="fk_service_release_service_id_service_definition",
        ),
        nullable=False,
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

    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "skill_artifact.id",
            use_alter=True,
            name="fk_skill_definition_current_artifact_id_skill_artifact",
        ),
    )
    platform_label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_skill_definition_active_key",
            "tenant_id",
            "key",
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
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id")
    )

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

    key: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    side_effect: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW")
    invocation_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="DIRECT")
    idempotency_semantics: Mapped[str] = mapped_column(String(64), nullable=False, default="NONE")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    __table_args__ = (
        Index(
            "uq_capability_definition_active_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        CheckConstraint(
            "side_effect IN ('none','read','write','destructive')",
            name="ck_capability_definition_side_effect",
        ),
        CheckConstraint(
            "risk_level IN ('LOW','MEDIUM','HIGH')",
            name="ck_capability_definition_risk_level",
        ),
        CheckConstraint(
            "invocation_policy IN ('DIRECT','EXECUTION_ONLY')",
            name="ck_capability_definition_invocation_policy",
        ),
        CheckConstraint(
            "invocation_policy = 'EXECUTION_ONLY' OR "
            "(side_effect IN ('none','read') AND risk_level IN ('LOW','MEDIUM'))",
            name="ck_capability_definition_direct_safety",
        ),
    )


class CapabilityImplementationModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "capability_implementation"

    capability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("capability_definition.id"), nullable=False
    )
    implementation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # `config` holds implementation identity/mapping fields only (T-21 attribution):
    # timeouts/retries live in execution_policy, paging in data_retrieval_policy,
    # and the credential source in the top-level auth_mode column.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    execution_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    data_retrieval_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ADR-057/D1: credential source lives in exactly one place (this column).
    # ADR-062: async submission support is an implementation capability marker that
    # the ServiceDraft step execution_mode is conjoined with at validation time.
    auth_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="NONE")
    async_submittable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    project_platform_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("project_platform.id")
    )
    shared_secret_ref: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        Index("ix_capability_impl_lookup", "capability_id", "enabled", "is_deleted"),
        Index("ix_capability_impl_platform", "tenant_id", "project_platform_id", "is_deleted"),
        CheckConstraint(
            "auth_mode IN ('USER_PLATFORM','SHARED_SECRET','NONE')",
            name="ck_capability_implementation_auth_mode",
        ),
        CheckConstraint(
            "implementation_type <> 'PLATFORM_SERVICE' OR project_platform_id IS NOT NULL",
            name="ck_capability_implementation_platform_service",
        ),
    )


class WorkspaceModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "workspace"

    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    backend_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SANDBOX")
    workspace_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
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
    agent_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    skill_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_definition.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (
        Index(
            "uq_agent_skill_binding_active",
            "tenant_id",
            "agent_definition_id",
            "skill_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentKnowledgeBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_knowledge_binding"
    agent_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    knowledge_source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("knowledge_source.id"), nullable=False
    )
    __table_args__ = (
        Index(
            "uq_agent_knowledge_binding_active",
            "tenant_id",
            "agent_definition_id",
            "knowledge_source_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentCapabilityBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_capability_binding"
    agent_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    capability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("capability_definition.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (
        Index(
            "uq_agent_capability_binding_active",
            "tenant_id",
            "agent_definition_id",
            "capability_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class AgentServiceBindingModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "agent_service_binding"
    agent_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    service_definition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (
        Index(
            "uq_agent_service_binding_active",
            "tenant_id",
            "agent_definition_id",
            "service_definition_id",
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
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    next_message_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(String(512))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")

    __table_args__ = (
        Index("ix_conversation_user_time", "platform_user_id", "create_time"),
        Index("ix_conversation_agent", "agent_id", "status"),
        Index("ix_conversation_last_message", "tenant_id", "last_message_at"),
        Index(
            "uq_conversation_active",
            "tenant_id",
            "platform_user_id",
            "agent_id",
            "origin_scope_key",
            unique=True,
            postgresql_where=text("is_deleted = false AND status = 'ACTIVE'"),
        ),
    )


class MessageModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "message"

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversation.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    content_ref: Mapped[str | None] = mapped_column(String(1024))
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="TEXT")
    external_message_id: Mapped[str | None] = mapped_column(String(512))
    verified_channel_account_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_account.id")
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id")
    )
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    trace_id: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_message_conversation_time", "conversation_id", "create_time"),
        Index("ix_message_trace", "trace_id"),
        Index(
            "uq_message_external",
            "tenant_id",
            "verified_channel_account_id",
            "external_message_id",
            unique=True,
            postgresql_where=text("external_message_id IS NOT NULL AND is_deleted = false"),
        ),
        Index(
            "uq_message_sequence",
            "tenant_id",
            "conversation_id",
            "sequence_no",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class ChannelDeliveryRouteModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "channel_delivery_route"

    platform_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversation.id"))
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_account_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel_account.id")
    )
    peer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

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
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # ADR-066: logical message identity across redelivery attempts. Regular enqueue
    # sets message_key = event_id; a redelivery keeps the original key and only
    # event_id gains a ":redeliver:<n>" suffix, so delivery_status aggregates per
    # logical message instead of being pinned to a historical failed attempt.
    message_key: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(256))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    dispatch_started_epoch: Mapped[int | None] = mapped_column(BigInteger)
    remote_idempotency: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(512))
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_channel_delivery_dedupe", "tenant_id", "dedupe_key", unique=True),
        Index("ix_channel_delivery_execution", "execution_id", "status"),
        Index("ix_channel_delivery_message", "tenant_id", "message_key", "attempt"),
    )


class ExecutionProposalModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "execution_proposal"

    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversation.id"), nullable=False
    )
    service_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_definition.id"), nullable=False
    )
    service_release_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_release.id"), nullable=False
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_snapshot.id"), nullable=False
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    resource_scope_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    template_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("message.id")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id")
    )

    __table_args__ = (
        Index(
            "uq_execution_proposal_pending_conversation",
            "tenant_id",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "is_deleted = false AND status = 'PENDING' AND superseded_at IS NULL"
            ),
        ),
        CheckConstraint(
            "(status = 'CONFIRMED' AND confirmed_message_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL AND execution_id IS NOT NULL) OR "
            "(status != 'CONFIRMED' AND confirmed_message_id IS NULL "
            "AND confirmed_at IS NULL AND execution_id IS NULL)",
            name="ck_execution_proposal_confirmed_fields",
        ),
    )


class ExecutionSnapshotModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """Frozen business projection for an execution (module 05 §3.3).

    One row is created at proposal/issue time and reused by the execution
    (service_execution.snapshot_id = proposal.snapshot_id), never duplicated.
    """

    __tablename__ = "execution_snapshot"

    service_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_definition.id")
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    service_release_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_release.id")
    )
    draft_revision: Mapped[int | None] = mapped_column(BigInteger)
    test_mode: Mapped[str | None] = mapped_column(String(16))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_ref: Mapped[str | None] = mapped_column(String(1024))

    __table_args__ = (
        Index(
            "uq_execution_snapshot_content",
            "tenant_id",
            "service_id",
            "source",
            "content_hash",
            unique=True,
            postgresql_where=text("service_id IS NOT NULL AND is_deleted = false"),
        ),
        CheckConstraint(
            "source IN ('FORMAL','TEST')",
            name="ck_execution_snapshot_source",
        ),
        CheckConstraint(
            "(source = 'FORMAL' AND service_id IS NOT NULL AND service_release_id IS NOT NULL "
            "AND draft_revision IS NULL AND test_mode IS NULL) "
            "OR (source = 'TEST' AND service_id IS NOT NULL AND service_release_id IS NULL "
            "AND draft_revision IS NOT NULL AND test_mode IN ('DRY_RUN','REAL_TEST'))",
            name="ck_execution_snapshot_source_shape",
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
    snapshot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_snapshot.id")
    )
    # ADR-063: CAPABILITY_TEST executions own capability test artifacts so that
    # artifact authorization keeps a single decision point (the execution), and
    # service_id/snapshot_id stay empty for that source.
    capability_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("capability_definition.id")
    )
    execution_source: Mapped[str] = mapped_column(String(16), nullable=False, default="FORMAL")
    test_mode: Mapped[str | None] = mapped_column(String(16))
    draft_revision: Mapped[int | None] = mapped_column(BigInteger)
    workspace_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workspace.id"))
    resource_scope_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # ADR-062: derived at compile time (any ASYNC step => ASYNC), never supplied by callers.
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="SYNC")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    current_step: Mapped[str | None] = mapped_column(String(256))
    current_step_name: Mapped[str | None] = mapped_column(String(128))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(256))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128))
    channel_source: Mapped[str | None] = mapped_column(String(32))
    waiting_reason: Mapped[str | None] = mapped_column(String(256))
    human_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    human_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_action: Mapped[str | None] = mapped_column(String(16))
    context_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    root_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id", use_alter=True,
                                         name="fk_service_execution_root_execution_id")
    )
    parent_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id", use_alter=True,
                                         name="fk_service_execution_parent_execution_id")
    )
    result_ref: Mapped[str | None] = mapped_column(String(1024))
    artifact_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list, server_default=text("'{}'")
    )
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        Index("ix_service_execution_capability", "tenant_id", "capability_id", "create_time"),
        Index("ix_service_execution_source", "tenant_id", "execution_source", "status"),
        CheckConstraint(
            "execution_source IN ('FORMAL','TEST','CAPABILITY_TEST')",
            name="ck_service_execution_execution_source",
        ),
        CheckConstraint(
            "execution_mode IN ('SYNC','ASYNC')",
            name="ck_service_execution_execution_mode",
        ),
        CheckConstraint(
            "(execution_source = 'FORMAL' AND service_id IS NOT NULL AND capability_id IS NULL "
            "AND snapshot_id IS NOT NULL AND service_release_id IS NOT NULL "
            "AND draft_revision IS NULL AND test_mode IS NULL) "
            "OR (execution_source = 'TEST' AND service_id IS NOT NULL AND capability_id IS NULL "
            "AND snapshot_id IS NOT NULL AND service_release_id IS NULL "
            "AND draft_revision IS NOT NULL AND test_mode IN ('DRY_RUN','REAL_TEST')) "
            "OR (execution_source = 'CAPABILITY_TEST' AND service_id IS NULL "
            "AND capability_id IS NOT NULL AND snapshot_id IS NULL "
            "AND service_release_id IS NULL AND draft_revision IS NULL "
            "AND test_mode IN ('DRY_RUN','REAL_TEST'))",
            name="ck_service_execution_source_shape",
        ),
        Index(
            "uq_service_execution_active_parent",
            "tenant_id",
            "parent_execution_id",
            unique=True,
            postgresql_where=text("parent_execution_id IS NOT NULL AND is_deleted = false"),
        ),
    )


class ExecutionStepModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "execution_step"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(256), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # operation_id is the logical call identity: it is generated when the step is
    # first created and reused across retries/derivations, binding the input hash
    # and the effect idempotency key (`effect:{operation_id}`).
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, default=uuid4)
    source_step_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_step.id")
    )
    checkpoint_ref: Mapped[str | None] = mapped_column(String(512))
    # ADR-062: explicit step declaration carried from the ServiceDraft via the
    # frozen snapshot; the Worker branches on this column, never on capability
    # semantics inferred at run time.
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="SYNC")
    # ADR-068: persistent cluster slot occupancy marker. Written in the same
    # transaction that claims a browser/external-scan/large-report slot, cleared
    # when the step's persistent occupancy ends (for ASYNC steps that is the
    # terminal state, *not* the lease release after submission).
    slot_resource_class: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(512))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_ref: Mapped[str | None] = mapped_column(String(1024))
    artifact_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list, server_default=text("'{}'")
    )
    wait_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_detail_ref: Mapped[str | None] = mapped_column(String(1024))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_execution_step_execution_status", "execution_id", "status"),
        Index("ix_execution_step_execution_sequence", "tenant_id", "execution_id", "sequence_no"),
        Index("ix_execution_step_wait", "tenant_id", "status", "wait_until", "is_deleted"),
        Index("ix_execution_step_slot", "slot_resource_class", "status"),
        CheckConstraint(
            "execution_mode IN ('SYNC','ASYNC')",
            name="ck_execution_step_execution_mode",
        ),
        CheckConstraint(
            "slot_resource_class IS NULL OR slot_resource_class IN "
            "('browser','external-scan','large-report')",
            name="ck_execution_step_slot_resource_class",
        ),
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
    """One logical async side effect per external submission (module 05 §3.3).

    A retry/derived execution reuses the same `operation_id` instead of creating a
    second external submission, so the identity here is the logical call, not the
    step row; that is why uniqueness is on operation_id/idempotency_key rather
    than on execution_step_id.
    """

    __tablename__ = "async_task_run"

    execution_step_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_step.id"), nullable=False
    )
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    capability_key: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    project_platform_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("project_platform.id")
    )
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    external_task_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUBMITTING")
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    poll_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_poll_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    reconcile_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_ref: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(128))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_async_task_run_operation",
            "tenant_id",
            "operation_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "uq_async_task_run_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_async_task_run_step", "execution_step_id"),
        Index("ix_async_task_run_poll", "status", "next_poll_at"),
        CheckConstraint(
            "external_task_id IS NOT NULL OR status IN "
            "('SUBMITTING','SUBMITTED_UNKNOWN','FAILED')",
            name="ck_async_task_run_external_task",
        ),
    )


class ExecutionCommandModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "execution_command"

    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(512))
    request_digest: Mapped[str | None] = mapped_column(String(128))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_progress_event_execution_time", "execution_id", "occurred_at"),)


class ArtifactModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "artifact"

    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id"), nullable=False
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id")
    )
    execution_step_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_step.id")
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # ADR-063: capability test artifacts are owned by a CAPABILITY_TEST execution,
    # so a single download contract (EXE-API-06) covers every artifact kind.
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    object_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="SYSTEM")

    __table_args__ = (
        Index("ix_artifact_execution", "execution_id", "execution_step_id"),
        Index("ix_artifact_execution_time", "tenant_id", "execution_id", "create_time"),
    )


class AuditLogModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "audit_log"

    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(256))
    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    before_digest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    after_digest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("service_execution.id")
    )
    result: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_audit_log_entity_time", "resource_type", "resource_id", "create_time"),
        Index("ix_audit_log_actor_time", "actor_user_id", "create_time"),
    )


class ConversationRunModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "conversation_run"

    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversation.id"), nullable=False
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    source_message_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    lease_owner: Mapped[str | None] = mapped_column(String(256))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_ref: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        Index("ix_conversation_run_conversation", "tenant_id", "conversation_id"),
        Index(
            "uq_conversation_run_message",
            "tenant_id",
            "source_message_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "uq_conversation_run_active",
            "tenant_id",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "is_deleted = false AND status IN ('RUNNING','CANCEL_REQUESTED')"
            ),
        ),
    )


class ChannelCommandReceiptModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "channel_command_receipt"

    message_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))
    command: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_digest: Mapped[str | None] = mapped_column(String(128))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index(
            "uq_channel_command_receipt_message",
            "tenant_id",
            "message_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "uq_channel_command_receipt_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class SkillImportPreviewModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    __tablename__ = "skill_import_preview"

    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("platform_user.id"), nullable=False
    )
    skill_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_definition.id")
    )
    expected_revision: Mapped[int | None] = mapped_column(BigInteger)
    expected_key: Mapped[str | None] = mapped_column(String(160))
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    staging_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    committed_skill_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_definition.id")
    )
    committed_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_artifact.id")
    )
    checksum: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("uq_skill_import_preview_token", "tenant_id", "token_hash", unique=True),
    )


class SkillArtifactValidationEventModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """Append-only review verdicts; never UPDATE the skill_artifact row itself."""

    __tablename__ = "skill_artifact_validation_event"

    artifact_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_artifact.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actor: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("platform_user.id"))

    __table_args__ = (
        Index("ix_skill_artifact_validation_event_artifact", "artifact_id", "create_time"),
    )


class SkillArtifactCapabilityModel(Base, IdMixin, SoftDeleteTimestampMixin, TenantMixin):
    """Immutable Artifact -> Capability dependency snapshot; importer writes only."""

    __tablename__ = "skill_artifact_capability"

    artifact_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("skill_artifact.id"), nullable=False
    )
    capability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("capability_definition.id"), nullable=False
    )
    capability_key_snapshot: Mapped[str] = mapped_column(String(256), nullable=False)

    __table_args__ = (
        Index(
            "uq_skill_artifact_capability_key",
            "tenant_id",
            "artifact_id",
            "capability_id",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


class WorkerSlotCounterModel(Base):
    """Cluster-level Resource Class slot counters (ADR-056 V1.14).

    A coordination table, **not** a business-fact table, and therefore the one
    deliberate exception to the framework-wide persistence contract (design
    module 06 §3.3): rows are permanent and never soft-deleted, the table is not
    tenant-scoped (a cluster-level limit is shared by every tenant), and
    ``resource_class`` is the primary key rather than a surrogate ``id``. The
    table always holds exactly 3 rows (one per class); it may be truncated and
    rebuilt at any time, with maintenance backfilling ``used`` by reconciliation.
    Adding ``is_deleted``/``tenant_id`` here would silently let the atomic
    ``UPDATE ... WHERE resource_class=:rc`` acquire the wrong row or leave
    orphaned counters behind.
    """

    __tablename__ = "worker_slot_counter"

    resource_class: Mapped[str] = mapped_column(String(32), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    slot_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=datetime.now
    )

    __table_args__ = (
        CheckConstraint("used >= 0 AND used <= slot_limit", name="ck_worker_slot_counter_used_range"),
    )
