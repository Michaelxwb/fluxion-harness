import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class PlatformUser(StandardColumnsMixin, Base):
    __tablename__ = "platform_user"
    __table_args__ = (
        sa.Index(
            "uq_platform_user_tenant_user_code",
            "tenant_id",
            "user_code",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_platform_user_tenant_status", "tenant_id", "status"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_code: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )


class ModelDefinition(StandardColumnsMixin, Base):
    __tablename__ = "model_definition"
    __table_args__ = (
        sa.Index(
            "uq_model_definition_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'OPENAI'"),
    )
    model_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    api_key: Mapped[str | None] = mapped_column(sa.Text())
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    last_test_status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'UNTESTED'"),
    )
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AgentAccessGrant(StandardColumnsMixin, Base):
    __tablename__ = "agent_access_grant"
    __table_args__ = (
        sa.Index(
            "uq_agent_access_grant_user_agent",
            "user_id",
            "agent_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_agent_access_grant_agent_user", "agent_id", "user_id"),
        {"schema": "control"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.agent_definition.id"),
        nullable=False,
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


class Skill(StandardColumnsMixin, Base):
    __tablename__ = "skill"
    __table_args__ = (
        sa.Index(
            "uq_skill_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_skill_user_scope_enabled", "user_scope", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    platform_label: Mapped[str | None] = mapped_column(sa.String(128))
    user_scope: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'SELECTED'"),
    )
    current_artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )


class SkillArtifact(StandardColumnsMixin, Base):
    __tablename__ = "skill_artifact"
    __table_args__ = (
        sa.Index(
            "uq_skill_artifact_skill_version",
            "skill_id",
            "version",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_skill_artifact_skill_checksum",
            "skill_id",
            "checksum",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_skill_artifact_skill_create_time", "skill_id", sa.text("create_time DESC")),
        {"schema": "control"},
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.skill.id"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    frontmatter_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    execution_mode: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'SYNC'"),
    )
    instructions: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("''"),
    )
    default_script: Mapped[str | None] = mapped_column(sa.String(256))
    package_size: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    validation_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    validation_message: Mapped[str | None] = mapped_column(sa.Text())
    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)


class AgentSkillBinding(StandardColumnsMixin, Base):
    __tablename__ = "agent_skill_binding"
    __table_args__ = (
        sa.Index(
            "uq_agent_skill_binding_agent_skill",
            "agent_id",
            "skill_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.agent_definition.id"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.skill.id"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )


class SkillUserGrant(StandardColumnsMixin, Base):
    __tablename__ = "skill_user_grant"
    __table_args__ = (
        sa.Index(
            "uq_skill_user_grant_skill_user",
            "skill_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_skill_user_grant_user_skill", "user_id", "skill_id"),
        {"schema": "control"},
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.skill.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)


class SkillImportIdempotency(StandardColumnsMixin, Base):
    """按 (tenant, Idempotency-Key, endpoint) 记录导入首次结果，供重放返回。"""

    __tablename__ = "skill_import_idempotency"
    __table_args__ = (
        sa.Index(
            "uq_skill_import_idempotency_tenant_key_endpoint",
            "tenant_id",
            "idempotency_key",
            "endpoint",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ConfigAuditLog(StandardColumnsMixin, Base):
    __tablename__ = "config_audit_log"
    __table_args__ = (
        sa.Index(
            "ix_config_audit_log_resource_create_time",
            "resource_type",
            "resource_id",
            sa.text("create_time DESC"),
        ),
        sa.Index(
            "ix_config_audit_log_actor_create_time",
            "actor_user_id",
            sa.text("create_time DESC"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    action: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))
    source_ip: Mapped[str | None] = mapped_column(sa.String(64))


class AgentDefinition(StandardColumnsMixin, Base):
    __tablename__ = "agent_definition"
    __table_args__ = (
        sa.Index(
            "uq_agent_definition_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_agent_definition_model_enabled", "model_id", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text())
    instructions: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.model_definition.id"),
        nullable=False,
    )
    runtime_config: Mapped[dict[str, Any]] = mapped_column(
        "runtime_config_json",
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )


class ProjectPlatform(StandardColumnsMixin, Base):
    __tablename__ = "project_platform"
    __table_args__ = (
        sa.Index(
            "uq_project_platform_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_project_platform_adapter_key_enabled", "adapter_key", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    resolver_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    resolver_config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    adapter_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    adapter_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    adapter_schema_version: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'1'"),
    )
    credential_mode: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    auth_secret: Mapped[str | None] = mapped_column(sa.Text())


class UserCredentialRef(StandardColumnsMixin, Base):
    __tablename__ = "user_credential_ref"
    __table_args__ = (
        sa.Index(
            "uq_user_credential_ref_user_platform",
            "user_id",
            "platform_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.platform_user.id"),
        nullable=False,
    )
    platform_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.project_platform.id"),
        nullable=False,
    )
    credential_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    credential_schema_version: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'1'"),
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class SharedCredentialRef(StandardColumnsMixin, Base):
    __tablename__ = "shared_credential_ref"
    __table_args__ = (
        sa.Index(
            "uq_shared_credential_ref_platform_id",
            "platform_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_shared_credential_ref_platform_status", "platform_id", "status"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("control.project_platform.id"),
        nullable=False,
    )
    credential_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    credential_schema_version: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=sa.text("'1'"),
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
