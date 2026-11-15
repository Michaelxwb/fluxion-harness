"""Rebuild the scheduling tables for scheduled-task-v2 (§3.3 / §4.4).

Development has no scheduling history, so the migration drops and recreates the tables
instead of carrying a compatibility layer; `task.workspace` / `workspace_lease` are kept.

Downgrade is intentionally unsupported: the old shape cannot represent a Schedule whose
configuration lives in immutable revisions, so restoring from backup is the only path.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.schema import SchemaItem

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

SCHEMA = "task"

#: 0049 时实际存在的调度表（其余是本轮新建）；按依赖倒序删除。
DROP_ORDER = (
    "runtime_result_outbox",
    "runtime_operation",
    "task_submission",
    "task_event",
    "task_execution",
    "task_schedule",
    "delivery_route",
)


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _drop_old_tables() -> None:
    for table in DROP_ORDER:
        op.drop_table(table, schema=SCHEMA)


def _create_delivery_route() -> None:
    op.create_table(
        "delivery_route",
        *_standard(),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("bot_id", sa.String(256), nullable=False),
        sa.Column("platform_user_id", sa.Uuid(), nullable=False),
        sa.Column("external_user_id", sa.String(256), nullable=False),
        sa.Column("external_conversation_id", sa.String(256)),
        sa.Column("route_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("route_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_delivery_route_route_hash",
        "delivery_route",
        ["route_hash"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_delivery_route_platform_user_channel_update_time",
        "delivery_route",
        ["platform_user_id", "channel", sa.text("update_time DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_delivery_route_bot_id_external_user_id",
        "delivery_route",
        ["bot_id", "external_user_id"],
        schema=SCHEMA,
    )


def _create_task_schedule() -> None:
    op.create_table(
        "task_schedule",
        *_standard(),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'ACTIVE'")),
        # 循环 FK：先建列，等 revision 表存在后再补约束。
        sa.Column("current_revision_id", sa.Uuid()),
        sa.Column("next_fire_at", sa.DateTime(timezone=True)),
        sa.Column("last_fire_at", sa.DateTime(timezone=True)),
        sa.Column("occurrence_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("config_version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("attention_reason", sa.String(64)),
        sa.Column("attention_since", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(32)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_schedule_due",
        "task_schedule",
        ["status", "next_fire_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'ACTIVE' AND is_deleted = false"),
    )
    op.create_index(
        "ix_task_schedule_owner",
        "task_schedule",
        ["tenant_id", "owner_user_id", "status", sa.text("update_time DESC")],
        schema=SCHEMA,
    )
    op.create_index("ix_task_schedule_agent", "task_schedule", ["agent_id", "status"], schema=SCHEMA)


def _create_task_schedule_revision() -> None:
    op.create_table(
        "task_schedule_revision",
        *_standard(),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("task.task_schedule.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("instruction_raw", sa.Text(), nullable=False),
        sa.Column("normalized_goal", sa.Text(), nullable=False),
        sa.Column("recurrence_kind", sa.String(16), nullable=False),
        sa.Column("cron_expr", sa.String(128)),
        sa.Column("run_at", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("schedule_policy", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("context_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("execution_spec", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("delivery_intent", sa.String(16), nullable=False, server_default=sa.text("'NONE'")),
        sa.Column("delivery_route_id", sa.Uuid(), sa.ForeignKey("task.delivery_route.id")),
        sa.Column("preflight_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("schedule_id", "revision_no", name="uq_task_schedule_revision_no"),
        sa.CheckConstraint(
            "(recurrence_kind = 'CRON' AND cron_expr IS NOT NULL AND run_at IS NULL) "
            "OR (recurrence_kind = 'ONCE' AND run_at IS NOT NULL AND cron_expr IS NULL)",
            name="ck_task_schedule_revision_recurrence",
        ),
        sa.CheckConstraint(
            "delivery_intent = 'NONE' OR delivery_route_id IS NOT NULL",
            name="ck_task_schedule_revision_delivery_route",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_schedule_revision_schedule",
        "task_schedule_revision",
        ["schedule_id", sa.text("revision_no DESC")],
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_task_schedule_current_revision",
        "task_schedule",
        "task_schedule_revision",
        ["current_revision_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )


def _create_task_schedule_draft() -> None:
    op.create_table(
        "task_schedule_draft",
        *_standard(),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("base_schedule_id", sa.Uuid(), sa.ForeignKey("task.task_schedule.id")),
        sa.Column("base_config_version", sa.BigInteger()),
        sa.Column("draft_hash", sa.String(71), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("preview_json", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_surface", sa.String(16)),
        sa.Column("confirmation_ref", sa.String(256)),
        sa.Column("confirmed_by_user_id", sa.Uuid()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("task.task_schedule.id")),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.CheckConstraint("draft_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_task_schedule_draft_draft_hash"),
        sa.CheckConstraint(
            "(kind = 'UPDATE' AND base_schedule_id IS NOT NULL AND base_config_version IS NOT NULL) "
            "OR (kind = 'CREATE' AND base_schedule_id IS NULL AND base_config_version IS NULL)",
            name="ck_task_schedule_draft_kind_shape",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_task_schedule_draft_idempotency",
        "task_schedule_draft",
        ["tenant_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_task_schedule_draft_confirmation",
        "task_schedule_draft",
        ["tenant_id", "confirmation_surface", "confirmation_ref"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'CONFIRMED' AND is_deleted = false"),
    )
    op.create_index(
        "ix_task_schedule_draft_owner_status",
        "task_schedule_draft",
        ["tenant_id", "owner_user_id", "status", "expires_at"],
        schema=SCHEMA,
    )


def _create_task_execution() -> None:
    op.create_table(
        "task_execution",
        *_standard(),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id")),
        sa.Column("root_id", sa.Uuid()),
        sa.Column("item_key", sa.String(128)),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("task.task_schedule.id")),
        sa.Column("schedule_fire_id", sa.Uuid()),
        sa.Column("schedule_revision_id", sa.Uuid(), sa.ForeignKey("task.task_schedule_revision.id")),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("source_operation_id", sa.Uuid()),
        sa.Column("source_tool_call_id", sa.String(256)),
        sa.Column(
            "completion_mode",
            sa.Enum("JOIN", "DETACH", native_enum=False, create_constraint=True, name="completion_mode"),
        ),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(16), nullable=False, server_default=sa.text("'SKILL'")),
        sa.Column("skill_id", sa.Uuid()),
        sa.Column("skill_artifact_id", sa.Uuid()),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("skill_execution_mode", sa.String(16)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("wait_kind", sa.String(24)),
        sa.Column("wait_payload_json", JSONB()),
        sa.Column("input_json", JSONB(), nullable=False),
        sa.Column("result_json", JSONB()),
        sa.Column("result_artifact_id", sa.Uuid()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("failure_class", sa.String(24)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "deadline_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
        sa.Column(
            "execution_snapshot_schema_version", sa.Integer(), nullable=False, server_default=sa.text("4")
        ),
        sa.Column("execution_snapshot_json", JSONB(), nullable=False),
        sa.Column("snapshot_hash", sa.String(128), nullable=False),
        sa.Column("snapshot_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("resume_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_resume_reason", sa.String(16)),
        sa.Column("last_checkpoint_id", sa.Uuid()),
        sa.CheckConstraint("lease_epoch >= 0", name="ck_task_execution_lease_epoch"),
        schema=SCHEMA,
    )
    # op.create_table 不带 ORM 的命名约定：枚举自带的 check 要用约定名重建，
    # 否则 ORM 与库里的约束名各说各话（schema parity 会红）。
    op.execute(
        "ALTER TABLE task.task_execution RENAME CONSTRAINT completion_mode "
        "TO ck_task_execution_completion_mode"
    )
    _execution_indexes()


def _execution_indexes() -> None:
    op.create_index(
        "uq_task_execution_tenant_idempotency_key",
        "task_execution",
        ["tenant_id", "idempotency_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_task_execution_parent_item_key",
        "task_execution",
        ["parent_id", "item_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("parent_id IS NOT NULL AND is_deleted = false"),
    )
    op.create_index(
        "uq_task_execution_root_fire",
        "task_execution",
        ["schedule_fire_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("schedule_fire_id IS NOT NULL AND parent_id IS NULL"),
    )
    op.create_index(
        "ix_task_execution_status_not_before_priority_create_time",
        "task_execution",
        ["status", "not_before", "priority", "create_time"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_execution_claim_scheduled",
        "task_execution",
        ["status", "scheduled_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("scheduled_at IS NOT NULL"),
    )
    op.create_index(
        "ix_task_execution_running_lease_until",
        "task_execution",
        ["lease_until"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "ix_task_execution_root_parent_status",
        "task_execution",
        ["root_id", "parent_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_execution_source_run",
        "task_execution",
        ["tenant_id", "source_run_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_task_execution_actor_user_create_time",
        "task_execution",
        ["actor_user_id", sa.text("create_time DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_execution_schedule_status_scheduled",
        "task_execution",
        ["schedule_id", "status", "scheduled_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_execution_schedule_create_time",
        "task_execution",
        ["schedule_id", sa.text("create_time DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_execution_waiting_external_sweep",
        "task_execution",
        ["wait_kind", "not_before"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'WAITING_EXTERNAL'"),
    )


def _create_fire() -> None:
    op.create_table(
        "schedule_fire",
        *_standard(),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("task.task_schedule.id"), nullable=False),
        sa.Column(
            "schedule_revision_id",
            sa.Uuid(),
            sa.ForeignKey("task.task_schedule_revision.id"),
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("execution_id", sa.Uuid()),
        sa.Column("skip_reason", sa.String(32)),
        sa.Column("missed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("dst_resolution", sa.String(24)),
        sa.Column("admission_error_code", sa.String(64)),
        sa.UniqueConstraint("tenant_id", "schedule_id", "scheduled_at", name="uq_schedule_fire_occurrence"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_schedule_fire_execution",
        "schedule_fire",
        ["execution_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("execution_id IS NOT NULL"),
    )
    op.create_index(
        "ix_schedule_fire_status_scheduled", "schedule_fire", ["status", "scheduled_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_schedule_fire_tenant_create",
        "schedule_fire",
        ["tenant_id", sa.text("create_time DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_schedule_fire_schedule_scheduled",
        "schedule_fire",
        ["schedule_id", sa.text("scheduled_at DESC")],
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_schedule_fire_execution",
        "schedule_fire",
        "task_execution",
        ["execution_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_task_execution_schedule_fire",
        "task_execution",
        "schedule_fire",
        ["schedule_fire_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )


def _create_checkpoint_and_human() -> None:
    op.create_table(
        "task_execution_checkpoint",
        *_standard(),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False),
        sa.Column("checkpoint_no", sa.Integer(), nullable=False),
        sa.Column("state_json", JSONB(), nullable=False),
        sa.UniqueConstraint("execution_id", "checkpoint_no", name="uq_task_execution_checkpoint_no"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_execution_checkpoint_exec",
        "task_execution_checkpoint",
        ["execution_id", sa.text("checkpoint_no DESC")],
        schema=SCHEMA,
    )
    op.create_table(
        "task_human_interaction",
        *_standard(),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("interaction_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("input_schema", JSONB()),
        sa.Column("response_json", JSONB()),
        sa.Column("delivery_route_id", sa.Uuid(), sa.ForeignKey("task.delivery_route.id")),
        sa.Column("source_message_id", sa.String(256)),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_task_human_interaction_open",
        "task_human_interaction",
        ["execution_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'OPEN' AND is_deleted = false"),
    )
    op.create_index(
        "ix_task_human_interaction_deadline",
        "task_human_interaction",
        ["status", "deadline_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_human_interaction_owner_deadline",
        "task_human_interaction",
        ["tenant_id", "owner_user_id", "status", "deadline_at"],
        schema=SCHEMA,
    )


def _create_delivery() -> None:
    op.create_table(
        "task_delivery",
        *_standard(),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False),
        sa.Column("delivery_route_id", sa.Uuid(), sa.ForeignKey("task.delivery_route.id"), nullable=False),
        sa.Column("delivery_kind", sa.String(24), nullable=False),
        sa.Column("delivery_key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_task_delivery_key",
        "task_delivery",
        ["tenant_id", "delivery_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_task_delivery_status_update_time", "task_delivery", ["status", "update_time"], schema=SCHEMA
    )
    op.create_index("ix_task_delivery_execution", "task_delivery", ["execution_id"], schema=SCHEMA)
    op.create_table(
        "task_delivery_attempt",
        *_standard(),
        sa.Column("delivery_id", sa.Uuid(), sa.ForeignKey("task.task_delivery.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("request_ref", sa.String(256)),
        sa.Column("response_ref", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("delivery_id", "attempt_no", name="uq_task_delivery_attempt_no"),
        schema=SCHEMA,
    )


def _create_event() -> None:
    op.create_table(
        "task_event",
        *_standard(),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("trace_id", sa.String(64)),
        sa.UniqueConstraint("tenant_id", "entity_type", "entity_id", "seq", name="uq_task_event_entity_seq"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_event_entity_create_time",
        "task_event",
        ["entity_type", "entity_id", "create_time"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_event_event_type_create_time",
        "task_event",
        ["event_type", sa.text("create_time DESC")],
        schema=SCHEMA,
    )


def _create_submission_and_operations() -> None:
    op.create_table(
        "task_submission",
        *_standard(),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("endpoint", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id")),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("task.task_schedule.id")),
        sa.Column(
            "schedule_draft_id",
            sa.Uuid(),
            sa.ForeignKey("task.task_schedule_draft.id"),
        ),
        sa.Column("response_json", JSONB(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_task_submission_tenant_key_endpoint",
        "task_submission",
        ["tenant_id", "idempotency_key", "endpoint"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_task_submission_task_create_time", "task_submission", ["task_id", "create_time"], schema=SCHEMA
    )
    op.create_index(
        "ix_task_submission_schedule_create_time",
        "task_submission",
        ["schedule_id", "create_time"],
        schema=SCHEMA,
    )
    op.create_table(
        "runtime_operation",
        *_standard(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column(
            "task_execution_id",
            sa.Uuid(),
            sa.ForeignKey(
                "task.task_execution.id", name="fk_runtime_operation_task_execution"
            ),
        ),
        sa.Column("source_tool_call_id", sa.String(256), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id")),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("submission_hash", sa.String(128)),
        sa.Column(
            "completion_mode",
            sa.Enum("JOIN", "DETACH", native_enum=False, create_constraint=True, name="completion_mode"),
        ),
        schema=SCHEMA,
    )
    op.execute(
        "ALTER TABLE task.runtime_operation RENAME CONSTRAINT completion_mode "
        "TO ck_runtime_operation_completion_mode"
    )
    op.create_index(
        "uq_runtime_operation_identity",
        "runtime_operation",
        ["tenant_id", "operation_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_table(
        "runtime_result_outbox",
        *_standard(),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False),
        sa.Column("task_event_id", sa.Uuid(), sa.ForeignKey("task.task_event.id"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("task_snapshot_hash", sa.String(128), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'PENDING'")),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0")),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.CheckConstraint("attempts >= 0", name="ck_runtime_result_outbox_counters"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_runtime_result_outbox_event",
        "runtime_result_outbox",
        ["tenant_id", "event_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_runtime_result_outbox_terminal",
        "runtime_result_outbox",
        ["task_event_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_runtime_result_outbox_pending",
        "runtime_result_outbox",
        ["not_before", "create_time", "id"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'PENDING' AND is_deleted = false"),
    )
    op.create_index(
        "ix_runtime_result_outbox_lease",
        "runtime_result_outbox",
        ["lease_until"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'PENDING' AND lease_until IS NOT NULL AND is_deleted = false"),
    )


def _alter_runtime_tables() -> None:
    op.add_column("tool_operation", sa.Column("owner_type", sa.String(16), nullable=False, server_default=sa.text("'RUN'")), schema="runtime")
    op.add_column("tool_operation", sa.Column("owner_task_execution_id", sa.Uuid()), schema="runtime")
    op.alter_column("tool_operation", "run_id", nullable=True, schema="runtime")
    op.create_check_constraint(
        "ck_tool_operation_owner",
        "tool_operation",
        "(owner_type = 'RUN' AND run_id IS NOT NULL AND owner_task_execution_id IS NULL) "
        "OR (owner_type = 'TASK' AND owner_task_execution_id IS NOT NULL AND run_id IS NULL)",
        schema="runtime",
    )
    op.create_index(
        "uq_tool_operation_task_owner_call",
        "tool_operation",
        ["tenant_id", "owner_task_execution_id", "source_tool_call_id"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("owner_task_execution_id IS NOT NULL AND is_deleted = false"),
    )
    op.drop_index("uq_tool_operation_run_call", table_name="tool_operation", schema="runtime")
    op.create_index(
        "uq_tool_operation_run_call",
        "tool_operation",
        ["tenant_id", "run_id", "source_tool_call_id"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("run_id IS NOT NULL AND is_deleted = false"),
    )

    op.add_column("tool_result_inbox", sa.Column("task_execution_id", sa.Uuid()), schema="runtime")
    op.add_column(
        "tool_result_inbox",
        sa.Column("receipt_event_type", sa.String(16), nullable=False, server_default=sa.text("'RUN_EVENT'")),
        schema="runtime",
    )
    op.alter_column("tool_result_inbox", "run_id", nullable=True, schema="runtime")
    op.drop_constraint(
        "tool_result_inbox_receipt_event_id_fkey",
        "tool_result_inbox",
        schema="runtime",
        type_="foreignkey",
    )
    op.create_check_constraint(
        "ck_tool_result_inbox_receipt_owner",
        "tool_result_inbox",
        "(receipt_event_type = 'RUN_EVENT' AND run_id IS NOT NULL AND task_execution_id IS NULL) "
        "OR (receipt_event_type = 'TASK_EVENT' AND task_execution_id IS NOT NULL AND run_id IS NULL)",
        schema="runtime",
    )

    op.add_column("model_call_lease", sa.Column("task_execution_id", sa.Uuid()), schema="runtime")
    op.add_column(
        "model_call_lease",
        sa.Column("owner_type", sa.String(16), nullable=False, server_default=sa.text("'RUN'")),
        schema="runtime",
    )
    op.drop_constraint("ck_model_call_lease_owner", "model_call_lease", schema="runtime", type_="check")
    op.create_check_constraint(
        "ck_model_call_lease_owner",
        "model_call_lease",
        "num_nonnulls(run_id, memory_job_id, probe_id, task_execution_id) = 1",
        schema="runtime",
    )
    op.create_check_constraint(
        "ck_model_call_lease_owner_type",
        "model_call_lease",
        "owner_type IN ('RUN','MEMORY_JOB','PROBE','TASK')",
        schema="runtime",
    )
    op.create_check_constraint(
        "ck_model_call_lease_task_owner",
        "model_call_lease",
        "(owner_type = 'TASK') = (task_execution_id IS NOT NULL)",
        schema="runtime",
    )
    op.create_index(
        "ix_artifact_task",
        "artifact",
        ["task_id"],
        schema="runtime",
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


def upgrade() -> None:
    _drop_old_tables()
    _create_delivery_route()
    _create_task_schedule()
    _create_task_schedule_revision()
    _create_task_schedule_draft()
    _create_task_execution()
    _create_fire()
    _create_checkpoint_and_human()
    _create_delivery()
    _create_event()
    _create_submission_and_operations()
    _alter_runtime_tables()


def downgrade() -> None:
    raise RuntimeError(
        "scheduled-task-v2 rebuilds the scheduling tables; restore from backup instead of downgrading"
    )
