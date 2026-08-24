from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, MetaData, String, Table

metadata = MetaData()

resource_definitions = Table(
    "resource_definitions",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("kind", String(64), primary_key=True),
    Column("resource_id", String(255), primary_key=True),
    Column("version", String(64), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("spec_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
)

Index(
    "idx_resource_latest_published",
    resource_definitions.c.tenant_id,
    resource_definitions.c.kind,
    resource_definitions.c.resource_id,
    resource_definitions.c.status,
    resource_definitions.c.published_at,
)

resource_bindings = Table(
    "resource_bindings",
    metadata,
    Column("binding_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("subject_type", String(32), nullable=False),
    Column("subject_id", String(128), nullable=False),
    Column("resource_type", String(64), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("resource_version_selector", String(64), nullable=False),
    Column("config_json", JSON, nullable=True),
    Column("credential_ref", String(512), nullable=True),
    Column("enabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_binding_subject",
    resource_bindings.c.tenant_id,
    resource_bindings.c.subject_type,
    resource_bindings.c.subject_id,
    resource_bindings.c.resource_type,
    resource_bindings.c.enabled,
)

audit_logs = Table(
    "audit_logs",
    metadata,
    Column("audit_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("publish_id", String(128), nullable=True),
    Column("action", String(64), nullable=False),
    Column("target_type", String(64), nullable=False),
    Column("target_id", String(255), nullable=False),
    Column("before_json", JSON, nullable=True),
    Column("after_json", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_audit_target",
    audit_logs.c.tenant_id,
    audit_logs.c.target_type,
    audit_logs.c.target_id,
    audit_logs.c.created_at,
)

publish_records = Table(
    "publish_records",
    metadata,
    Column("publish_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("resource_type", String(64), nullable=False),
    Column("resource_id", String(255), nullable=False),
    Column("version", String(64), nullable=False),
    Column("operation", String(32), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("trace_id", String(128), nullable=False),
    Column("event_id", String(128), nullable=False, unique=True),
    Column("publish_note", String(1000), nullable=True),
    Column("approval_id", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_publish_record_resource",
    publish_records.c.tenant_id,
    publish_records.c.resource_type,
    publish_records.c.resource_id,
    publish_records.c.created_at,
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("event_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", String(255), nullable=False),
    Column("version", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("locked_by", String(128), nullable=True),
    Column("locked_until", DateTime(timezone=True), nullable=True),
    Column("last_error", String(1000), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
)

Index(
    "idx_outbox_dispatch",
    outbox_events.c.status,
    outbox_events.c.available_at,
    outbox_events.c.created_at,
)

config_revisions = Table(
    "config_revisions",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

platform_users = Table(
    "platform_users",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("platform_user_id", String(128), primary_key=True),
    Column("display_name", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

chat_access_tokens = Table(
    "chat_access_tokens",
    metadata,
    Column("access_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("platform_user_id", String(128), nullable=False),
    Column("runtime_profile_id", String(255), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

Index(
    "idx_chat_access_user",
    chat_access_tokens.c.tenant_id,
    chat_access_tokens.c.platform_user_id,
    chat_access_tokens.c.revoked_at,
)

channel_identities = Table(
    "channel_identities",
    metadata,
    Column("tenant_id", String(128), primary_key=True),
    Column("channel_type", String(64), primary_key=True),
    Column("channel_user_id", String(255), primary_key=True),
    Column("platform_user_id", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index(
    "idx_channel_identity_platform_user",
    channel_identities.c.tenant_id,
    channel_identities.c.platform_user_id,
)

bind_codes = Table(
    "bind_codes",
    metadata,
    Column("bind_code_id", String(128), primary_key=True),
    Column("tenant_id", String(128), nullable=False),
    Column("platform_user_id", String(128), nullable=False),
    Column("code_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("failed_attempts", Integer, nullable=False),
    Column("frozen_at", DateTime(timezone=True), nullable=True),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index("idx_bind_code_tenant_expiry", bind_codes.c.tenant_id, bind_codes.c.expires_at)
