from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _standard_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "create_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "update_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def _create_control_platform_user() -> None:
    op.create_table(
        "platform_user",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_code", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        schema="control",
    )
    op.create_index(
        "uq_platform_user_tenant_user_code",
        "platform_user",
        ["tenant_id", "user_code"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_platform_user_tenant_status",
        "platform_user",
        ["tenant_id", "status"],
        schema="control",
    )


def _create_control_model_definition() -> None:
    op.create_table(
        "model_definition",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=False, server_default=sa.text("'OPENAI'")),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.String(256)),
        sa.Column("params_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "last_test_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'UNTESTED'"),
        ),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        schema="control",
    )
    op.create_index(
        "uq_model_definition_tenant_key",
        "model_definition",
        ["tenant_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_control_agent_definition() -> None:
    op.create_table(
        "agent_definition",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("control.model_definition.id"), nullable=False),
        sa.Column(
            "runtime_config_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema="control",
    )
    op.create_index(
        "uq_agent_definition_tenant_key",
        "agent_definition",
        ["tenant_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_agent_definition_model_enabled",
        "agent_definition",
        ["model_id", "enabled"],
        schema="control",
    )


def _create_control_skill() -> None:
    op.create_table(
        "skill",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("platform_label", sa.String(128)),
        sa.Column("user_scope", sa.String(16), nullable=False, server_default=sa.text("'SELECTED'")),
        sa.Column("current_artifact_id", sa.Uuid()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema="control",
    )
    op.create_index(
        "uq_skill_tenant_key",
        "skill",
        ["tenant_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_skill_user_scope_enabled",
        "skill",
        ["user_scope", "enabled"],
        schema="control",
    )


def _create_control_skill_artifact() -> None:
    op.create_table(
        "skill_artifact",
        *_standard_columns(),
        sa.Column("skill_id", sa.Uuid(), sa.ForeignKey("control.skill.id"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("frontmatter_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("manifest_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("execution_mode", sa.String(16), nullable=False, server_default=sa.text("'SYNC'")),
        sa.Column("default_script", sa.String(256)),
        sa.Column("package_size", sa.BigInteger(), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("validation_message", sa.Text()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        schema="control",
    )
    op.create_index(
        "uq_skill_artifact_skill_version",
        "skill_artifact",
        ["skill_id", "version"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_skill_artifact_skill_checksum",
        "skill_artifact",
        ["skill_id", "checksum"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_skill_artifact_skill_create_time",
        "skill_artifact",
        ["skill_id", sa.text("create_time DESC")],
        schema="control",
    )


def _create_control_agent_skill_binding() -> None:
    op.create_table(
        "agent_skill_binding",
        *_standard_columns(),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("control.agent_definition.id"), nullable=False),
        sa.Column("skill_id", sa.Uuid(), sa.ForeignKey("control.skill.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema="control",
    )
    op.create_index(
        "uq_agent_skill_binding_agent_skill",
        "agent_skill_binding",
        ["agent_id", "skill_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_control_mcp_server() -> None:
    op.create_table(
        "mcp_server",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "transport",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'streamable-http'"),
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("auth_secret_ref", sa.String(256)),
        sa.Column("auth_config_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("user_scope", sa.String(16), nullable=False, server_default=sa.text("'SELECTED'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "connection_status",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
        sa.Column("tool_catalog_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tool_catalog_hash", sa.String(128)),
        sa.Column("tool_catalog_revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True)),
        sa.Column("last_discovery_error", sa.Text()),
        sa.Column("connect_timeout_ms", sa.Integer(), nullable=False, server_default=sa.text("5000")),
        sa.Column("tool_cache_ttl_sec", sa.Integer(), nullable=False, server_default=sa.text("300")),
        schema="control",
    )
    op.create_index(
        "uq_mcp_server_tenant_key",
        "mcp_server",
        ["tenant_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_mcp_server_user_scope_enabled",
        "mcp_server",
        ["user_scope", "enabled"],
        schema="control",
    )


def _create_control_agent_mcp_binding() -> None:
    op.create_table(
        "agent_mcp_binding",
        *_standard_columns(),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("control.agent_definition.id"), nullable=False),
        sa.Column("mcp_server_id", sa.Uuid(), sa.ForeignKey("control.mcp_server.id"), nullable=False),
        schema="control",
    )
    op.create_index(
        "uq_agent_mcp_binding_agent_mcp",
        "agent_mcp_binding",
        ["agent_id", "mcp_server_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_control_agent_access_grant() -> None:
    op.create_table(
        "agent_access_grant",
        *_standard_columns(),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("control.platform_user.id"), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("control.agent_definition.id"), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="control",
    )
    op.create_index(
        "uq_agent_access_grant_user_agent",
        "agent_access_grant",
        ["user_id", "agent_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_agent_access_grant_agent_user",
        "agent_access_grant",
        ["agent_id", "user_id"],
        schema="control",
    )


def _create_control_skill_user_grant() -> None:
    op.create_table(
        "skill_user_grant",
        *_standard_columns(),
        sa.Column("skill_id", sa.Uuid(), sa.ForeignKey("control.skill.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("control.platform_user.id"), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        schema="control",
    )
    op.create_index(
        "uq_skill_user_grant_skill_user",
        "skill_user_grant",
        ["skill_id", "user_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_skill_user_grant_user_skill",
        "skill_user_grant",
        ["user_id", "skill_id"],
        schema="control",
    )


def _create_control_mcp_user_grant() -> None:
    op.create_table(
        "mcp_user_grant",
        *_standard_columns(),
        sa.Column("mcp_server_id", sa.Uuid(), sa.ForeignKey("control.mcp_server.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("control.platform_user.id"), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=False),
        schema="control",
    )
    op.create_index(
        "uq_mcp_user_grant_mcp_user",
        "mcp_user_grant",
        ["mcp_server_id", "user_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_mcp_user_grant_user_mcp",
        "mcp_user_grant",
        ["user_id", "mcp_server_id"],
        schema="control",
    )


def _create_control_bot_account() -> None:
    op.create_table(
        "bot_account",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False, server_default=sa.text("'WECOM'")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("bot_id", sa.String(256), nullable=False),
        sa.Column("secret_ref", sa.String(256), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("control.agent_definition.id"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_connected_at", sa.DateTime(timezone=True)),
        schema="control",
    )
    op.create_index(
        "uq_bot_account_bot_id",
        "bot_account",
        ["bot_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_bot_account_agent_channel_enabled",
        "bot_account",
        ["agent_id", "channel", "enabled"],
        schema="control",
    )


def _create_control_channel_identity() -> None:
    op.create_table(
        "channel_identity",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("identity_key", sa.String(512), nullable=False),
        sa.Column("external_user_id", sa.String(256), nullable=False),
        sa.Column("bot_account_id", sa.Uuid(), sa.ForeignKey("control.bot_account.id"), nullable=False),
        sa.Column(
            "platform_user_id",
            sa.Uuid(),
            sa.ForeignKey("control.platform_user.id"),
            nullable=False,
        ),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        schema="control",
    )
    op.create_index(
        "uq_channel_identity_tenant_identity_key",
        "channel_identity",
        ["tenant_id", "identity_key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_channel_identity_platform_user_channel",
        "channel_identity",
        ["platform_user_id", "channel"],
        schema="control",
    )


def _create_control_bind_code() -> None:
    op.create_table(
        "bind_code",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "platform_user_id",
            sa.Uuid(),
            sa.ForeignKey("control.platform_user.id"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_channel_identity_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        schema="control",
    )
    op.create_index(
        "uq_bind_code_code_hash",
        "bind_code",
        ["code_hash"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_bind_code_platform_user_status_expires",
        "bind_code",
        ["platform_user_id", "status", "expires_at"],
        schema="control",
    )


def _create_control_project_platform() -> None:
    op.create_table(
        "project_platform",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("resolver_type", sa.String(32), nullable=False),
        sa.Column("resolver_config_json", JSONB(), nullable=False),
        sa.Column("adapter_key", sa.String(128), nullable=False),
        sa.Column("adapter_config_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "adapter_schema_version",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'1'"),
        ),
        sa.Column("credential_mode", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema="control",
    )
    op.create_index(
        "uq_project_platform_tenant_key",
        "project_platform",
        ["tenant_id", "key"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_project_platform_adapter_key_enabled",
        "project_platform",
        ["adapter_key", "enabled"],
        schema="control",
    )


def _create_control_user_credential_ref() -> None:
    op.create_table(
        "user_credential_ref",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("control.platform_user.id"), nullable=False),
        sa.Column(
            "platform_id",
            sa.Uuid(),
            sa.ForeignKey("control.project_platform.id"),
            nullable=False,
        ),
        sa.Column("secret_ref", sa.String(256), nullable=False),
        sa.Column(
            "credential_schema_version",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'1'"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        schema="control",
    )
    op.create_index(
        "uq_user_credential_ref_user_platform",
        "user_credential_ref",
        ["user_id", "platform_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_control_shared_credential_ref() -> None:
    op.create_table(
        "shared_credential_ref",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "platform_id",
            sa.Uuid(),
            sa.ForeignKey("control.project_platform.id"),
            nullable=False,
        ),
        sa.Column("secret_ref", sa.String(256), nullable=False),
        sa.Column(
            "credential_schema_version",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'1'"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        schema="control",
    )
    op.create_index(
        "uq_shared_credential_ref_platform_id",
        "shared_credential_ref",
        ["platform_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_shared_credential_ref_platform_status",
        "shared_credential_ref",
        ["platform_id", "status"],
        schema="control",
    )


def _create_control_config_audit_log() -> None:
    op.create_table(
        "config_audit_log",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("before_json", JSONB()),
        sa.Column("after_json", JSONB()),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("source_ip", sa.String(64)),
        schema="control",
    )
    op.create_index(
        "ix_config_audit_log_resource_create_time",
        "config_audit_log",
        ["resource_type", "resource_id", sa.text("create_time DESC")],
        schema="control",
    )
    op.create_index(
        "ix_config_audit_log_actor_create_time",
        "config_audit_log",
        ["actor_user_id", sa.text("create_time DESC")],
        schema="control",
    )


def _create_runtime_conversation() -> None:
    op.create_table(
        "conversation",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_run_id", sa.Uuid()),
        schema="runtime",
    )
    op.create_index(
        "ix_conversation_tenant_user_agent_update_time",
        "conversation",
        ["tenant_id", "user_id", "agent_id", sa.text("update_time DESC")],
        schema="runtime",
    )


def _create_runtime_run_record() -> None:
    op.create_table(
        "run_record",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("runtime.conversation.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True)),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        schema="runtime",
    )
    op.create_index(
        "uq_run_record_active_conversation",
        "run_record",
        ["conversation_id"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text(
            "status IN ('CREATED', 'RUNNING', 'WAITING_INPUT') AND is_deleted = false"
        ),
    )
    op.create_index(
        "ix_run_record_conversation_create_time",
        "run_record",
        ["conversation_id", sa.text("create_time DESC")],
        schema="runtime",
    )
    op.create_index(
        "ix_run_record_agent_status_create_time",
        "run_record",
        ["agent_id", "status", sa.text("create_time DESC")],
        schema="runtime",
    )
    op.create_index(
        "ix_run_record_running_lease_until",
        "run_record",
        ["lease_until"],
        schema="runtime",
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index("ix_run_record_trace_id", "run_record", ["trace_id"], schema="runtime")


def _create_runtime_runtime_snapshot() -> None:
    op.create_table(
        "runtime_snapshot",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("agent_revision", sa.BigInteger(), nullable=False),
        sa.Column("model_revision", sa.BigInteger(), nullable=False),
        sa.Column("agent_json", JSONB(), nullable=False),
        sa.Column("model_json", JSONB(), nullable=False),
        sa.Column("skill_catalog_json", JSONB(), nullable=False),
        sa.Column("mcp_catalog_json", JSONB(), nullable=False),
        sa.Column("policy_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("prompt_template_version", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_runtime_snapshot_run_id"),
        schema="runtime",
    )
    op.create_index(
        "ix_runtime_snapshot_content_hash",
        "runtime_snapshot",
        ["content_hash"],
        schema="runtime",
    )


def _create_runtime_canonical_event() -> None:
    op.create_table(
        "canonical_event",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("artifact_id", sa.Uuid()),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_canonical_event_conversation_seq"),
        schema="runtime",
    )
    op.create_index("ix_canonical_event_run_seq", "canonical_event", ["run_id", "seq"], schema="runtime")
    op.create_index(
        "ix_canonical_event_conversation_create_time",
        "canonical_event",
        ["conversation_id", "create_time"],
        schema="runtime",
    )


def _create_runtime_user_memory() -> None:
    op.create_table(
        "user_memory",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("memory_key", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("content_json", JSONB(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(256)),
        sa.Column(
            "write_policy",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'CONTROLLED'"),
        ),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema="runtime",
    )
    op.create_index(
        "uq_user_memory_tenant_user_memory_key",
        "user_memory",
        ["tenant_id", "user_id", "memory_key"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_user_memory_user_enabled_update_time",
        "user_memory",
        ["user_id", "enabled", sa.text("update_time DESC")],
        schema="runtime",
    )


def _create_runtime_artifact() -> None:
    op.create_table(
        "artifact",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("conversation_id", sa.Uuid()),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("preview_text", sa.Text()),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "(run_id IS NOT NULL) <> (task_id IS NOT NULL)", name="ck_artifact_run_xor_task"
        ),
        schema="runtime",
    )
    op.create_index(
        "ix_artifact_run_create_time",
        "artifact",
        ["run_id", "create_time"],
        schema="runtime",
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_artifact_task_create_time",
        "artifact",
        ["task_id", "create_time"],
        schema="runtime",
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_artifact_conversation_create_time",
        "artifact",
        ["conversation_id", "create_time"],
        schema="runtime",
        postgresql_where=sa.text("conversation_id IS NOT NULL"),
    )


def _create_runtime_run_interrupt() -> None:
    op.create_table(
        "run_interrupt",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("interrupt_type", sa.String(32), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("options_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'WAITING'")),
        sa.Column("resolution_json", JSONB()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        schema="runtime",
    )
    op.create_index("ix_run_interrupt_run_status", "run_interrupt", ["run_id", "status"], schema="runtime")
    op.create_index(
        "ix_run_interrupt_conversation_status",
        "run_interrupt",
        ["conversation_id", "status"],
        schema="runtime",
    )


def _create_runtime_tool_call_audit() -> None:
    op.create_table(
        "tool_call_audit",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("conversation_id", sa.Uuid()),
        sa.Column("tool_call_id", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("tool_kind", sa.String(32), nullable=False),
        sa.Column("prepared_args_hash", sa.String(128), nullable=False),
        sa.Column("args_preview_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True)),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("latency_ms", sa.BigInteger()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("artifact_id", sa.Uuid()),
        sa.CheckConstraint(
            "run_id IS NOT NULL OR task_id IS NOT NULL", name="ck_tool_call_audit_run_or_task"
        ),
        schema="runtime",
    )
    op.create_index(
        "uq_tool_call_audit_run_tool_call",
        "tool_call_audit",
        ["run_id", "tool_call_id"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("run_id IS NOT NULL AND is_deleted = false"),
    )
    op.create_index(
        "uq_tool_call_audit_task_tool_call",
        "tool_call_audit",
        ["task_id", "tool_call_id"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("task_id IS NOT NULL AND is_deleted = false"),
    )
    op.create_index(
        "ix_tool_call_audit_run_create_time",
        "tool_call_audit",
        ["run_id", "create_time"],
        schema="runtime",
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_tool_call_audit_task_create_time",
        "tool_call_audit",
        ["task_id", "create_time"],
        schema="runtime",
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_tool_call_audit_tool_name_status_create_time",
        "tool_call_audit",
        ["tool_name", "status", sa.text("create_time DESC")],
        schema="runtime",
    )


def _create_runtime_egress_audit() -> None:
    op.create_table(
        "egress_audit",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("skill_artifact_id", sa.Uuid()),
        sa.Column("platform_id", sa.Uuid()),
        sa.Column("adapter_key", sa.String(128)),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("operation", sa.String(256)),
        sa.Column("method", sa.String(16)),
        sa.Column("policy_decision", sa.String(16), nullable=False),
        sa.Column("credential_ref_id", sa.Uuid()),
        sa.Column("status_code", sa.Integer()),
        sa.Column("result_status", sa.String(24), nullable=False),
        sa.Column("latency_ms", sa.BigInteger()),
        sa.Column("error_code", sa.String(64)),
        sa.CheckConstraint("run_id IS NOT NULL OR task_id IS NOT NULL", name="ck_egress_audit_run_or_task"),
        schema="runtime",
    )
    op.create_index(
        "ix_egress_audit_run_create_time",
        "egress_audit",
        ["run_id", "create_time"],
        schema="runtime",
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_egress_audit_task_create_time",
        "egress_audit",
        ["task_id", "create_time"],
        schema="runtime",
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_egress_audit_user_create_time",
        "egress_audit",
        ["user_id", sa.text("create_time DESC")],
        schema="runtime",
    )
    op.create_index(
        "ix_egress_audit_platform_result_create_time",
        "egress_audit",
        ["platform_id", "result_status", sa.text("create_time DESC")],
        schema="runtime",
    )


def _create_runtime_model_invocation_audit() -> None:
    op.create_table(
        "model_invocation_audit",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("retry_reason", sa.String(64)),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("latency_ms", sa.BigInteger()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.CheckConstraint(
            "run_id IS NOT NULL OR task_id IS NOT NULL",
            name="ck_model_invocation_audit_run_or_task",
        ),
        schema="runtime",
    )
    op.create_index(
        "ix_model_invocation_audit_run_attempt",
        "model_invocation_audit",
        ["run_id", "attempt"],
        schema="runtime",
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_model_invocation_audit_task_attempt",
        "model_invocation_audit",
        ["task_id", "attempt"],
        schema="runtime",
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_model_invocation_audit_provider_model_status_create_time",
        "model_invocation_audit",
        ["provider", "model", "status", sa.text("create_time DESC")],
        schema="runtime",
    )


def _create_task_delivery_route() -> None:
    op.create_table(
        "delivery_route",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("bot_id", sa.String(256), nullable=False),
        sa.Column("platform_user_id", sa.Uuid(), nullable=False),
        sa.Column("external_user_id", sa.String(256), nullable=False),
        sa.Column("external_conversation_id", sa.String(256)),
        sa.Column("route_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("route_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        schema="task",
    )
    op.create_index(
        "uq_delivery_route_route_hash",
        "delivery_route",
        ["route_hash"],
        unique=True,
        schema="task",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_delivery_route_platform_user_channel_update_time",
        "delivery_route",
        ["platform_user_id", "channel", sa.text("update_time DESC")],
        schema="task",
    )
    op.create_index(
        "ix_delivery_route_bot_id_external_user_id",
        "delivery_route",
        ["bot_id", "external_user_id"],
        schema="task",
    )


def _create_task_task_schedule() -> None:
    op.create_table(
        "task_schedule",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("intent_key", sa.String(128), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("input_template_json", JSONB(), nullable=False),
        sa.Column("schedule_type", sa.String(16), nullable=False, server_default=sa.text("'CRON'")),
        sa.Column("cron_expr", sa.String(128)),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True)),
        sa.Column(
            "delivery_route_id",
            sa.Uuid(),
            sa.ForeignKey("task.delivery_route.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("next_fire_at", sa.DateTime(timezone=True)),
        sa.Column("last_fire_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(schedule_type = 'CRON' AND cron_expr IS NOT NULL) "
            "OR (schedule_type = 'ONCE' AND run_at IS NOT NULL)",
            name="ck_task_schedule_trigger",
        ),
        schema="task",
    )
    op.create_index(
        "ix_task_schedule_status_next_fire_at",
        "task_schedule",
        ["status", "next_fire_at"],
        schema="task",
    )
    op.create_index(
        "ix_task_schedule_actor_user_status",
        "task_schedule",
        ["actor_user_id", "status"],
        schema="task",
    )
    op.create_index(
        "ix_task_schedule_agent_status",
        "task_schedule",
        ["agent_id", "status"],
        schema="task",
    )


def _create_task_task_execution() -> None:
    op.create_table(
        "task_execution",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id")),
        sa.Column("root_id", sa.Uuid()),
        sa.Column("item_key", sa.String(128)),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("task.task_schedule.id")),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("intent_key", sa.String(128), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("skill_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("task_type", sa.String(24), nullable=False, server_default=sa.text("'SKILL'")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_json", JSONB(), nullable=False),
        sa.Column("result_json", JSONB()),
        sa.Column("result_artifact_id", sa.Uuid()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("external_ref_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "deadline_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
        sa.Column(
            "execution_snapshot_schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("execution_snapshot_json", JSONB(), nullable=False),
        sa.Column("snapshot_hash", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "not_before",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "delivery_route_id",
            sa.Uuid(),
            sa.ForeignKey("task.delivery_route.id"),
        ),
        sa.Column(
            "delivery_mode",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'FINAL_ONLY'"),
        ),
        sa.Column(
            "delivery_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("delivery_key", sa.String(128), nullable=False),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        schema="task",
    )
    op.create_index(
        "uq_task_execution_tenant_idempotency_key",
        "task_execution",
        ["tenant_id", "idempotency_key"],
        unique=True,
        schema="task",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_task_execution_parent_item_key",
        "task_execution",
        ["parent_id", "item_key"],
        unique=True,
        schema="task",
        postgresql_where=sa.text("parent_id IS NOT NULL AND is_deleted = false"),
    )
    op.create_index(
        "ix_task_execution_status_not_before_priority_create_time",
        "task_execution",
        ["status", "not_before", "priority", "create_time"],
        schema="task",
    )
    op.create_index(
        "ix_task_execution_running_lease_until",
        "task_execution",
        ["lease_until"],
        schema="task",
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "ix_task_execution_root_parent_status",
        "task_execution",
        ["root_id", "parent_id", "status"],
        schema="task",
    )
    op.create_index(
        "ix_task_execution_actor_user_create_time",
        "task_execution",
        ["actor_user_id", sa.text("create_time DESC")],
        schema="task",
    )
    op.create_index(
        "ix_task_execution_schedule_create_time",
        "task_execution",
        ["schedule_id", sa.text("create_time DESC")],
        schema="task",
    )
    op.create_index(
        "ix_task_execution_final_delivery",
        "task_execution",
        ["delivery_status", "delivered_at"],
        schema="task",
        postgresql_where=sa.text("delivery_mode = 'FINAL_ONLY' AND is_deleted = false"),
    )


def _create_task_task_event() -> None:
    op.create_table(
        "task_event",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("trace_id", sa.String(64)),
        sa.UniqueConstraint("task_id", "seq", name="uq_task_event_task_seq"),
        schema="task",
    )
    op.create_index("ix_task_event_task_create_time", "task_event", ["task_id", "create_time"], schema="task")
    op.create_index(
        "ix_task_event_event_type_create_time",
        "task_event",
        ["event_type", sa.text("create_time DESC")],
        schema="task",
    )


def upgrade() -> None:
    _create_control_platform_user()
    _create_control_model_definition()
    _create_control_agent_definition()
    _create_control_skill()
    _create_control_skill_artifact()
    _create_control_agent_skill_binding()
    _create_control_mcp_server()
    _create_control_agent_mcp_binding()
    _create_control_agent_access_grant()
    _create_control_skill_user_grant()
    _create_control_mcp_user_grant()
    _create_control_bot_account()
    _create_control_channel_identity()
    _create_control_bind_code()
    _create_control_project_platform()
    _create_control_user_credential_ref()
    _create_control_shared_credential_ref()
    _create_control_config_audit_log()
    _create_runtime_conversation()
    _create_runtime_run_record()
    _create_runtime_runtime_snapshot()
    _create_runtime_canonical_event()
    _create_runtime_user_memory()
    _create_runtime_artifact()
    _create_runtime_run_interrupt()
    _create_runtime_tool_call_audit()
    _create_runtime_egress_audit()
    _create_runtime_model_invocation_audit()
    _create_task_delivery_route()
    _create_task_task_schedule()
    _create_task_task_execution()
    _create_task_task_event()


def downgrade() -> None:
    op.drop_table("task_event", schema="task")
    op.drop_table("task_execution", schema="task")
    op.drop_table("task_schedule", schema="task")
    op.drop_table("delivery_route", schema="task")
    op.drop_table("model_invocation_audit", schema="runtime")
    op.drop_table("egress_audit", schema="runtime")
    op.drop_table("tool_call_audit", schema="runtime")
    op.drop_table("run_interrupt", schema="runtime")
    op.drop_table("artifact", schema="runtime")
    op.drop_table("user_memory", schema="runtime")
    op.drop_table("canonical_event", schema="runtime")
    op.drop_table("runtime_snapshot", schema="runtime")
    op.drop_table("run_record", schema="runtime")
    op.drop_table("conversation", schema="runtime")
    op.drop_table("config_audit_log", schema="control")
    op.drop_table("shared_credential_ref", schema="control")
    op.drop_table("user_credential_ref", schema="control")
    op.drop_table("project_platform", schema="control")
    op.drop_table("bind_code", schema="control")
    op.drop_table("channel_identity", schema="control")
    op.drop_table("bot_account", schema="control")
    op.drop_table("mcp_user_grant", schema="control")
    op.drop_table("skill_user_grant", schema="control")
    op.drop_table("agent_access_grant", schema="control")
    op.drop_table("agent_mcp_binding", schema="control")
    op.drop_table("mcp_server", schema="control")
    op.drop_table("agent_skill_binding", schema="control")
    op.drop_table("skill_artifact", schema="control")
    op.drop_table("skill", schema="control")
    op.drop_table("agent_definition", schema="control")
    op.drop_table("model_definition", schema="control")
    op.drop_table("platform_user", schema="control")
