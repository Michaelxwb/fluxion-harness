"""Stable memory identities, audited mutation protocol, and durable jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.schema import SchemaItem

from migrations.memory_redesign_data import migrate_memory_data, preflight_memory

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _create_scope() -> None:
    op.create_table(
        "memory_scope",
        *_standard(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("forget_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memory_scope_user"),
        sa.CheckConstraint("is_deleted = false", name="ck_memory_scope_not_deleted"),
        sa.CheckConstraint("revision >= 0 AND forget_epoch >= 0", name="ck_memory_scope_revision"),
        schema="runtime",
    )


def _create_history() -> None:
    op.create_table(
        "user_memory_history",
        *_standard(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("memory_key", sa.String(128), nullable=False),
        sa.Column("origin_id", sa.Uuid(), sa.ForeignKey("runtime.user_memory.id"), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("change_reason", sa.String(32), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128)),
        sa.Column("command_id", sa.String(256), nullable=False),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("after_json", JSONB(), nullable=False),
        sa.Column("legacy_snapshots_json", JSONB()),
        sa.UniqueConstraint("origin_id", "version", name="uq_user_memory_history_origin_version"),
        sa.CheckConstraint("is_deleted = false", name="ck_user_memory_history_not_deleted"),
        schema="runtime",
    )
    op.create_index(
        "ix_user_memory_history_key_version",
        "user_memory_history",
        ["tenant_id", "user_id", "memory_key", sa.text("version DESC")],
        schema="runtime",
    )


def _create_receipt() -> None:
    op.create_table(
        "memory_command_receipt",
        *_standard(),
        sa.Column("command_id", sa.String(256), nullable=False),
        sa.Column("endpoint", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_json", JSONB(), nullable=False),
        sa.Column("source_run_id", sa.Uuid()),
        sa.UniqueConstraint("tenant_id", "command_id", name="uq_memory_command_receipt_command"),
        sa.CheckConstraint("is_deleted = false", name="ck_memory_command_receipt_not_deleted"),
        schema="runtime",
    )


def _create_job() -> None:
    op.create_table(
        "memory_consolidation_job",
        *_standard(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("through_seq", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("claim_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("result_json", JSONB()),
        sa.Column("enqueued_scope_revision", sa.BigInteger(), nullable=False),
        sa.Column("input_scope_revision", sa.BigInteger()),
        sa.Column("input_json", JSONB()),
        sa.Column("input_hash", sa.String(128)),
        sa.Column("resume_ref_json", JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("tenant_id", "run_id", name="uq_memory_job_run"),
        sa.CheckConstraint("is_deleted = false", name="ck_memory_consolidation_job_not_deleted"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','RETRY','DONE','SKIPPED','FAILED')",
            name="ck_memory_consolidation_job_status",
        ),
        sa.CheckConstraint("claim_epoch >= 0 AND attempt >= 0", name="ck_memory_consolidation_job_counters"),
        schema="runtime",
    )
    _index(
        "ix_memory_job_claim",
        "memory_consolidation_job",
        ["tenant_id", "next_attempt_at", "create_time"],
        "status IN ('PENDING','RETRY')",
    )
    _index(
        "ix_memory_job_lease", "memory_consolidation_job", ["tenant_id", "lease_until"], "status = 'RUNNING'"
    )


def _index(
    name: str,
    table: str,
    columns: list[str | sa.TextClause],
    predicate: str | None = None,
    unique: bool = False,
) -> None:
    op.create_index(
        name,
        table,
        columns,
        unique=unique,
        schema="runtime",
        postgresql_where=sa.text(predicate) if predicate else None,
    )


def _extend_memory() -> None:
    columns: list[SchemaItem] = [
        sa.Column("author", sa.String(16), nullable=False, server_default=sa.text("'AGENT'")),
        sa.Column("explicit_request", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("confidence", sa.SmallInteger(), nullable=False, server_default=sa.text("2")),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("source_event_id", sa.Uuid()),
        sa.Column("source_seq", sa.BigInteger()),
        sa.Column("source_quote", sa.String(512)),
        sa.Column("confirmation_event_id", sa.Uuid()),
        sa.Column("retirement_reason", sa.String(32)),
        sa.Column("retirement_revision", sa.BigInteger()),
        sa.Column("last_source_run_id", sa.Uuid()),
        sa.Column("last_source_seq", sa.BigInteger()),
    ]
    for column in columns:
        assert isinstance(column, sa.Column)
        op.add_column("user_memory", column, schema="runtime")
    op.alter_column(
        "user_memory", "write_policy", server_default=sa.text("'SERVER_CONTROLLED'"), schema="runtime"
    )
    op.add_column("run_interrupt", sa.Column("proposal_json", JSONB()), schema="runtime")


def _memory_constraints() -> None:
    checks = {
        "author": "author IN ('USER','ADMIN','AGENT')",
        "category": "category IN ('PREFERENCE','WORK_STYLE','EXPLICIT','TASK_STATE')",
        "counters": "version >= 1 AND confidence BETWEEN 1 AND 5 AND confirmed_count >= 0",
        "agent_policy": "author != 'AGENT' OR (confidence = 2 AND explicit_request = false AND expires_at IS NOT NULL)",
        "user_confirmation": "author != 'USER' OR (explicit_request = true AND confirmation_event_id IS NOT NULL)",
        "admin_confirmation": "author != 'ADMIN' OR explicit_request = false",
        "write_policy": "write_policy = 'SERVER_CONTROLLED'",
    }
    for name, condition in checks.items():
        op.create_check_constraint("ck_user_memory_" + name, "user_memory", condition, schema="runtime")


def _new_indexes() -> None:
    _index("uq_user_memory_key", "user_memory", ["tenant_id", "user_id", "memory_key"], unique=True)
    _index(
        "ix_user_memory_candidates",
        "user_memory",
        ["tenant_id", "user_id", "category", sa.text("update_time DESC")],
        "is_deleted = false AND enabled = true",
    )
    _index(
        "ix_user_memory_expiry",
        "user_memory",
        ["tenant_id", "expires_at", "user_id"],
        "is_deleted = false AND expires_at IS NOT NULL",
    )
    _index(
        "uq_memory_consolidated_run",
        "canonical_event",
        ["tenant_id", "run_id"],
        "event_type = 'MEMORY_CONSOLIDATED' AND is_deleted = false",
        unique=True,
    )
    _index(
        "uq_run_interrupt_pending_memory_confirm",
        "run_interrupt",
        ["tenant_id", "run_id"],
        "interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING' AND is_deleted = false",
        unique=True,
    )
    _index(
        "ix_memory_confirm_expiry",
        "run_interrupt",
        ["tenant_id", "run_id", "expires_at"],
        "interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'",
    )


def upgrade() -> None:
    bind = op.get_bind()
    preflight_memory(bind)
    _extend_memory()
    _create_scope()
    _create_history()
    _create_receipt()
    _create_job()
    migrate_memory_data(bind)
    op.drop_index("uq_user_memory_tenant_user_memory_key", schema="runtime")
    op.drop_index("ix_user_memory_user_enabled_update_time", schema="runtime")
    op.drop_column("user_memory", "source_type", schema="runtime")
    _memory_constraints()
    _new_indexes()


def downgrade() -> None:
    raise RuntimeError("MEMORY_MIGRATION_BACKUP_RESTORE_REQUIRED: restore the maintenance-window backup")
