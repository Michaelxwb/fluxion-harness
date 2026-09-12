"""V1.12 foundation alignment

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11

Aligns the physical schema with the V1.12 design baseline:
- drops the retired ``external_auth_profile`` / ``user_service_auth`` /
  ``integration_registration`` tables (authorization converges on
  ``agent_access_grant``; IntegrationRegistration never persists);
- adds ``tenant_id`` (server default 'default') to every framework-owned
  table and re-scopes unique indexes to tenant prefix;
- renames drifted columns to the frozen vocabulary
  (release_no / implementation_type / artifact_ref / external_message_id /
  resource_type / identity_scope_key / channel_type / user_key / *_key);
- adds Human Checkpoint + trace/governance columns to ``service_execution``;
- creates the missing foundation tables (project_platform /
  user_project_credential / agent_access_grant / bind_code / skill_definition /
  async_task_run / channel_delivery / auth_account / auth_session).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_DEFAULT = "default"


def _tenant_columns() -> list[str]:
    return [
        "channel_account",
        "channel_identity",
        "channel_binding",
        "model_config",
        "agent_definition",
        "service_definition",
        "service_release",
        "skill_artifact",
        "knowledge_source",
        "capability_definition",
        "capability_implementation",
        "workspace",
        "agent_skill_binding",
        "agent_knowledge_binding",
        "agent_capability_binding",
        "agent_service_binding",
        "conversation",
        "message",
        "channel_delivery_route",
        "execution_snapshot",
        "service_execution",
        "execution_step",
        "execution_command",
        "task_progress_event",
        "artifact",
    ]


def _drop_indexes() -> None:
    dropped = [
        "uq_channel_account_active",
        "uq_channel_identity_active",
        "uq_model_config_active_name",
        "uq_agent_definition_active_name",
        "uq_service_definition_active_key",
        "uq_service_release_release_id",
        "uq_service_release_hash",
        "uq_capability_definition_active_name",
        "uq_execution_step_attempt_active",
        "uq_service_execution_active_idempotency",
        "uq_execution_command_active_idempotency",
        "ix_agent_skill_binding_active",
        "uq_agent_skill_binding_active",
        "uq_agent_knowledge_binding_active",
        "uq_agent_capability_binding_active",
        "uq_agent_service_binding_active",
        "uq_workspace_owner",
    ]
    for name in dropped:
        op.execute(f"DROP INDEX IF EXISTS {name}")


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return bool(bind.dialect.has_table(bind, name))



def upgrade() -> None:
    # ---- retired tables -------------------------------------------------
    op.execute("DROP TABLE IF EXISTS external_auth_profile")
    op.execute("DROP TABLE IF EXISTS user_service_auth")
    op.execute("DROP TABLE IF EXISTS integration_registration")

    # ---- tenant scoping --------------------------------------------------
    for table in _tenant_columns():
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128) "
            f"NOT NULL DEFAULT '{TENANT_DEFAULT}'"
        )
    op.execute(
        "UPDATE audit_log SET tenant_id = 'default' WHERE tenant_id IS NULL"
    )
    op.execute(
        "ALTER TABLE audit_log ALTER COLUMN tenant_id SET NOT NULL, "
        "ALTER COLUMN tenant_id SET DEFAULT 'default'"
    )
    op.execute(
        f"ALTER TABLE platform_user ALTER COLUMN tenant_id SET DEFAULT '{TENANT_DEFAULT}'"
    )
    op.execute(
        f"ALTER TABLE user_memory ALTER COLUMN tenant_id SET DEFAULT '{TENANT_DEFAULT}'"
    )

    # ---- dropped unique indexes must go before renames -------------------
    _drop_indexes()

    # ---- column renames ---------------------------------------------------
    op.execute("DO $$ BEGIN ALTER TABLE service_release RENAME COLUMN release_id TO release_no; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE capability_implementation RENAME COLUMN impl_type TO implementation_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute(
        "DO $$ BEGIN ALTER TABLE capability_implementation RENAME COLUMN timeout_policy TO execution_policy; "
        "EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$"
    )
    op.execute("DO $$ BEGIN ALTER TABLE skill_artifact RENAME COLUMN package_ref TO artifact_ref; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE message RENAME COLUMN channel_message_ref TO external_message_id; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE audit_log RENAME COLUMN entity_type TO resource_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE audit_log RENAME COLUMN entity_id TO resource_id; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE channel_identity RENAME COLUMN peer_type TO identity_scope_key; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE channel_identity RENAME COLUMN peer_id TO external_user_id; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE channel_account RENAME COLUMN channel TO channel_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE conversation RENAME COLUMN channel TO channel_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE channel_delivery_route RENAME COLUMN channel TO channel_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE platform_user RENAME COLUMN external_platform_user_ref TO user_key; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("ALTER TABLE capability_implementation DROP COLUMN IF EXISTS retry_policy")
    op.execute("ALTER TABLE capability_implementation ADD COLUMN IF NOT EXISTS data_retrieval_policy JSONB")

    # ---- new columns on existing tables ----------------------------------
    op.execute("ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'END_USER'")
    op.execute("ALTER TABLE platform_user ALTER COLUMN status SET DEFAULT 'ACTIVE'")
    op.execute("ALTER TABLE agent_definition ADD COLUMN IF NOT EXISTS agent_key VARCHAR(160)")
    op.execute("ALTER TABLE capability_definition ADD COLUMN IF NOT EXISTS capability_key VARCHAR(160)")
    op.execute("ALTER TABLE service_definition ADD COLUMN IF NOT EXISTS primary_agent_id UUID REFERENCES agent_definition(id)")
    op.execute("ALTER TABLE service_definition ADD COLUMN IF NOT EXISTS execution_type VARCHAR(32) NOT NULL DEFAULT 'HYBRID'")
    op.execute("ALTER TABLE skill_artifact ADD COLUMN IF NOT EXISTS sdk_version VARCHAR(64)")
    op.execute("ALTER TABLE skill_artifact ADD COLUMN IF NOT EXISTS entrypoint VARCHAR(256)")
    op.execute("ALTER TABLE skill_artifact ADD COLUMN IF NOT EXISTS manifest JSONB")
    op.execute("ALTER TABLE skill_artifact ADD COLUMN IF NOT EXISTS validation_status VARCHAR(32) NOT NULL DEFAULT 'PENDING'")
    op.execute("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES agent_definition(id)")
    op.execute("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS title VARCHAR(256)")
    op.execute("ALTER TABLE channel_binding ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ")

    # user_memory: restructure to memory_key/memory_value vocabulary
    op.execute("DO $$ BEGIN ALTER TABLE user_memory RENAME COLUMN memory_type TO memory_key; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE user_memory RENAME COLUMN content TO memory_value; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE user_memory RENAME COLUMN source TO source_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE user_memory ALTER COLUMN memory_key SET NOT NULL")
    op.execute("ALTER TABLE user_memory ALTER COLUMN source_type SET NOT NULL")

    # service_execution: Human Checkpoint + trace/governance fields
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS service_id UUID REFERENCES service_definition(id)")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS primary_agent_id UUID REFERENCES agent_definition(id)")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES conversation(id)")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS channel_source VARCHAR(32)")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS waiting_reason VARCHAR(256)")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS human_deadline TIMESTAMPTZ")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS requested_action VARCHAR(16)")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE service_execution ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0")

    # execution_step: external task facts move to async_task_run
    op.execute("ALTER TABLE execution_step DROP COLUMN IF EXISTS external_task_id")

    # backfill stable keys from display names where derivable
    op.execute("UPDATE agent_definition SET agent_key = name WHERE agent_key IS NULL")
    op.execute("UPDATE capability_definition SET capability_key = name WHERE capability_key IS NULL")
    op.execute("ALTER TABLE agent_definition ALTER COLUMN agent_key SET NOT NULL")
    op.execute("ALTER TABLE capability_definition ALTER COLUMN capability_key SET NOT NULL")

    op.execute("ALTER TABLE skill_artifact DROP COLUMN IF EXISTS scope")
    op.execute(
        "ALTER TABLE execution_snapshot ADD COLUMN IF NOT EXISTS service_release_id UUID "
        "REFERENCES service_release(id)"
    )

    # ---- tenant-scoped unique indexes (partial: is_deleted = false) -------
    def uq(name: str, table: str, cols: str) -> None:
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({cols}) "
            "WHERE is_deleted = false"
        )

    uq("uq_platform_user_active_key", "platform_user", "tenant_id, user_key")
    uq("uq_channel_account_active", "channel_account", "tenant_id, channel_type, account_key")
    uq(
        "uq_channel_identity_active",
        "channel_identity",
        "tenant_id, channel_account_id, identity_scope_key, external_user_id",
    )
    uq("uq_model_config_active_name", "model_config", "tenant_id, name")
    uq("uq_agent_definition_active_key", "agent_definition", "tenant_id, agent_key")
    uq("uq_capability_definition_active_key", "capability_definition", "tenant_id, capability_key")
    uq("uq_service_definition_active_key", "service_definition", "tenant_id, service_key")
    uq("uq_service_release_no", "service_release", "tenant_id, service_id, release_no")
    uq("uq_service_release_hash", "service_release", "tenant_id, service_id, content_hash")
    uq(
        "uq_user_memory_active_key",
        "user_memory",
        "tenant_id, platform_user_id, memory_key",
    )
    uq(
        "uq_execution_step_active",
        "execution_step",
        "tenant_id, execution_id, step_key",
    )
    uq(
        "uq_service_execution_active_idempotency",
        "service_execution",
        "tenant_id, idempotency_key",
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_command_active_idempotency "
        "ON execution_command (tenant_id, idempotency_key) "
        "WHERE is_deleted = false AND idempotency_key IS NOT NULL"
    )
    binding_targets = {
        "agent_skill_binding": "skill_id",
        "agent_knowledge_binding": "knowledge_source_id",
        "agent_capability_binding": "capability_id",
        "agent_service_binding": "service_id",
    }
    for table, target in binding_targets.items():
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_active ON {table} (tenant_id, agent_id, {target}) "
            "WHERE is_deleted = false"
        )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_active_owner "
        "ON workspace (tenant_id, owner_type, owner_id) WHERE is_deleted = false"
    )

    # ---- new tables --------------------------------------------------------
    if not _table_exists("auth_account"):
        op.create_table(
        "auth_account",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="BUILDER"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_account_active_username "
        "ON auth_account (tenant_id, username) WHERE is_deleted = false"
    )

    if not _table_exists("auth_session"):
        op.create_table(
        "auth_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("auth_account.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_session_token ON auth_session (token_hash)")

    if not _table_exists("project_platform"):
        op.create_table(
        "project_platform",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("auth_schema", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_project_platform_active_key "
        "ON project_platform (tenant_id, key) WHERE is_deleted = false"
    )

    if not _table_exists("user_project_credential"):
        op.create_table(
        "user_project_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column(
            "platform_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_user.id"),
            nullable=False,
        ),
        sa.Column(
            "project_platform_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_platform.id"),
            nullable=False,
        ),
        sa.Column("credential_ref", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="UNVERIFIED"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_project_credential_active "
        "ON user_project_credential (tenant_id, platform_user_id, project_platform_id) "
        "WHERE is_deleted = false"
    )

    if not _table_exists("agent_access_grant"):
        op.create_table(
        "agent_access_grant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column(
            "platform_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_user.id"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_definition.id"),
            nullable=False,
        ),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("platform_user.id")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_access_grant_active "
        "ON agent_access_grant (tenant_id, platform_user_id, agent_id) "
        "WHERE is_deleted = false"
    )

    if not _table_exists("bind_code"):
        op.create_table(
        "bind_code",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column(
            "platform_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_user.id"),
            nullable=False,
        ),
        sa.Column("channel_type", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column(
            "used_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_identity.id"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_bind_code_hash ON bind_code (tenant_id, code_hash)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bind_code_single_pending "
        "ON bind_code (tenant_id, platform_user_id, channel_type) "
        "WHERE is_deleted = false AND status = 'PENDING'"
    )

    if not _table_exists("skill_definition"):
        op.create_table(
        "skill_definition",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column("skill_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("platform_label", sa.String(256), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_definition_active_key "
        "ON skill_definition (tenant_id, skill_key) WHERE is_deleted = false"
    )
    op.execute("ALTER TABLE skill_artifact ADD COLUMN IF NOT EXISTS skill_id UUID REFERENCES skill_definition(id)")
    op.execute(
        "ALTER TABLE skill_definition ADD COLUMN IF NOT EXISTS latest_artifact_id UUID "
        "REFERENCES skill_artifact(id)"
    )
    uq(
        "uq_skill_artifact_checksum",
        "skill_artifact",
        "tenant_id, skill_id, checksum",
    )

    if not _table_exists("async_task_run"):
        op.create_table(
        "async_task_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_execution.id"),
            nullable=False,
        ),
        sa.Column(
            "execution_step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_step.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("external_task_id", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="SUBMITTED"),
        sa.Column("next_poll_at", sa.DateTime(timezone=True)),
        sa.Column("poll_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_async_task_run_step "
        "ON async_task_run (tenant_id, execution_step_id) WHERE is_deleted = false"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_async_task_run_poll ON async_task_run (status, next_poll_at)")

    if not _table_exists("channel_delivery"):
        op.create_table(
        "channel_delivery",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, server_default=TENANT_DEFAULT),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_execution.id"),
            nullable=False,
        ),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("execution_step.id")),
        sa.Column(
            "delivery_route_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_delivery_route.id"),
            nullable=False,
        ),
        sa.Column("channel_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_delivery_dedupe ON channel_delivery (tenant_id, dedupe_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_channel_delivery_execution ON channel_delivery (execution_id, status)")


def downgrade() -> None:
    # Foundation alignment is a baseline rewrite; downgrade drops the new
    # tables and reverts renames best-effort.
    for table in (
        "channel_delivery",
        "async_task_run",
        "skill_definition",
        "bind_code",
        "agent_access_grant",
        "user_project_credential",
        "project_platform",
        "auth_session",
        "auth_account",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DO $$ BEGIN ALTER TABLE service_release RENAME COLUMN release_no TO release_id; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE capability_implementation RENAME COLUMN implementation_type TO impl_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE skill_artifact RENAME COLUMN artifact_ref TO package_ref; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE message RENAME COLUMN external_message_id TO channel_message_ref; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE audit_log RENAME COLUMN resource_type TO entity_type; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
    op.execute("DO $$ BEGIN ALTER TABLE audit_log RENAME COLUMN resource_id TO entity_id; EXCEPTION WHEN undefined_column OR duplicate_column THEN NULL; END $$")
