"""Durable async-tool operations and Run continuation checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

ACTIVE_RUN = "status IN ('CREATED','RUNNING','WAITING_TOOL','WAITING_INPUT') AND is_deleted = false"
OUTBOX_PENDING = "status = 'PENDING' AND is_deleted = false"
OUTBOX_LEASE = "status = 'PENDING' AND lease_until IS NOT NULL AND is_deleted = false"


def _standard():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _enum(name, *values):
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def _outbox(table):
    return [
        sa.Column("status", _enum(f"ck_{table}_outbox_status", "PENDING", "SENT", "FAILED"),
                  nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
    ]


def _index(name, table, columns, schema="runtime", predicate="is_deleted = false", unique=False):
    op.create_index(name, table, columns, schema=schema, unique=unique, postgresql_where=sa.text(predicate))


def _upgrade_existing():
    op.add_column("run_record", sa.Column("deadline_at", sa.DateTime(timezone=True)), schema="runtime")
    op.add_column("run_record", sa.Column("execution_epoch", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")), schema="runtime")
    op.create_check_constraint("ck_run_record_run_status", "run_record",
        "status IN ('CREATED','RUNNING','WAITING_TOOL','WAITING_INPUT','COMPLETED','FAILED','CANCELLED')",
        schema="runtime")
    op.drop_index("uq_run_record_active_conversation", schema="runtime")
    _index("uq_run_record_active_conversation", "run_record", ["conversation_id"],
           predicate=ACTIVE_RUN, unique=True)
    _index("ix_run_record_waiting_deadline", "run_record", ["deadline_at"],
           predicate="status IN ('WAITING_TOOL','WAITING_INPUT') AND is_deleted = false")
    op.add_column("canonical_event", sa.Column("source_event_id", sa.Uuid()), schema="runtime")
    _index("uq_canonical_event_source", "canonical_event", ["tenant_id", "source_event_id"],
           predicate="source_event_id IS NOT NULL AND is_deleted = false", unique=True)
    op.add_column("task_execution", sa.Column("source_operation_id", sa.Uuid()), schema="task")
    op.add_column("task_execution", sa.Column("source_tool_call_id", sa.String(256)), schema="task")
    op.add_column("task_execution", sa.Column("completion_mode", sa.String(6)), schema="task")
    op.create_check_constraint("ck_task_execution_completion_mode", "task_execution",
                               "completion_mode IN ('JOIN','DETACH')", schema="task")


def _create_tool_operation():
    op.create_table("tool_operation", *_standard(),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runtime.run_record.id"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_tool_call_id", sa.String(256), nullable=False),
        sa.Column("completion_mode", _enum("ck_tool_operation_completion_mode", "JOIN", "DETACH"), nullable=False),
        sa.Column("status", _enum("ck_tool_operation_operation_status", "SUBMIT_PENDING", "SUBMITTED", "TASK_ACCEPTED",
                  "RUNNING", "RESULT_RECEIVED", "MATERIALIZED", "COMPLETED", "FAILED", "CANCELLED", "LATE"),
                  nullable=False, server_default=sa.text("'SUBMIT_PENDING'")),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("task_snapshot_hash", sa.String(128), nullable=False),
        sa.Column("submission_json", JSONB(), nullable=False),
        sa.Column("input_hash", sa.String(128), nullable=False),
        sa.Column("terminal_event_id", sa.Uuid()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_phase", _enum("ck_tool_operation_error_phase", "SUBMIT", "EXECUTE")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)), schema="runtime")
    _index("uq_tool_operation_run_call", "tool_operation", ["tenant_id", "run_id", "source_tool_call_id"],
           unique=True)
    _index("uq_tool_operation_task", "tool_operation", ["tenant_id", "task_id"], unique=True,
           predicate="task_id IS NOT NULL AND is_deleted = false")
    _index("ix_tool_operation_pending", "tool_operation", ["tenant_id", "run_id", "status", "completion_mode"],
           predicate="status IN ('SUBMIT_PENDING','SUBMITTED','TASK_ACCEPTED','RUNNING',"
                     "'RESULT_RECEIVED','MATERIALIZED') AND is_deleted = false")


def _create_run_continuation():
    op.create_table("run_continuation", *_standard(),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runtime.run_record.id"), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("runtime.runtime_snapshot.id"), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False, server_default=sa.text("'BEFORE_MODEL'")),
        sa.Column("wait_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("context_upto_seq", sa.BigInteger(), nullable=False),
        sa.Column("consumed_event_seq", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("turns", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_tokens", sa.BigInteger()), sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("checkpoint_schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("runner_state_json", JSONB(), nullable=False),
        sa.CheckConstraint("phase = 'BEFORE_MODEL'", name="ck_run_continuation_phase"),
        sa.CheckConstraint("wait_generation >= 0 AND context_upto_seq >= 0 AND consumed_event_seq >= 0 "
                           "AND turns >= 0 AND tool_calls >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
                           name="ck_run_continuation_counters"), schema="runtime")
    _index("uq_run_continuation_run", "run_continuation", ["run_id"], unique=True)
    _index("ix_run_continuation_ready", "run_continuation", ["run_id"],
           predicate="ready = true AND is_deleted = false")


def _create_tool_result_inbox():
    op.create_table("tool_result_inbox", *_standard(),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runtime.run_record.id"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("runtime.tool_operation.id"), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("task_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("payload_hash", sa.String(128), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("receipt_event_id", sa.Uuid(), sa.ForeignKey("runtime.canonical_event.id"), nullable=False),
        sa.Column("receipt_seq", sa.BigInteger(), nullable=False),
        sa.Column("canonical_event_id", sa.Uuid(), sa.ForeignKey("runtime.canonical_event.id")),
        sa.Column("materialized", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint("task_event_seq > 0 AND receipt_seq > 0", name="ck_tool_result_inbox_sequences"),
        sa.CheckConstraint("NOT materialized OR canonical_event_id IS NOT NULL",
                           name="ck_tool_result_inbox_materialized"), schema="runtime")
    _index("uq_tool_result_inbox_event", "tool_result_inbox", ["tenant_id", "event_id"], unique=True)
    _index("ix_tool_result_inbox_ready", "tool_result_inbox", ["tenant_id", "run_id", "receipt_seq"],
           predicate="materialized = false AND late = false AND is_deleted = false")


def _create_control_outbox():
    op.create_table("tool_control_outbox", *_standard(),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("runtime.tool_operation.id"), nullable=False),
        sa.Column("command", _enum("ck_tool_control_outbox_command", "SUBMIT", "CANCEL_OPERATION"), nullable=False),
        *_outbox("tool_control_outbox"),
        sa.CheckConstraint("attempts >= 0", name="ck_tool_control_outbox_attempts"), schema="runtime")
    _index("uq_tool_control_outbox_command", "tool_control_outbox", ["operation_id", "command"], unique=True)
    _index("ix_tool_control_outbox_pending", "tool_control_outbox", ["not_before", "create_time", "id"],
           predicate=OUTBOX_PENDING)
    _index("ix_tool_control_outbox_lease", "tool_control_outbox", ["lease_until"], predicate=OUTBOX_LEASE)


def _create_worker_tables():
    op.create_table("runtime_operation", *_standard(),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_tool_call_id", sa.String(256), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id")),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("submission_hash", sa.String(128)),
        sa.Column("completion_mode", _enum("ck_runtime_operation_completion_mode", "JOIN", "DETACH")), schema="task")
    _index("uq_runtime_operation_identity", "runtime_operation", ["tenant_id", "operation_id"],
           schema="task", unique=True)
    op.create_table("runtime_result_outbox", *_standard(),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False),
        sa.Column("task_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("task_snapshot_hash", sa.String(128), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(128), nullable=False), *_outbox("runtime_result_outbox"),
        sa.ForeignKeyConstraint(["task_id", "task_event_seq"], ["task.task_event.task_id", "task.task_event.seq"]),
        sa.CheckConstraint("attempts >= 0 AND task_event_seq > 0", name="ck_runtime_result_outbox_counters"),
        schema="task")
    _index("uq_runtime_result_outbox_event", "runtime_result_outbox", ["tenant_id", "event_id"],
           schema="task", unique=True)
    _index("uq_runtime_result_outbox_terminal", "runtime_result_outbox", ["task_id", "task_event_seq"],
           schema="task", unique=True)
    _index("ix_runtime_result_outbox_pending", "runtime_result_outbox", ["not_before", "create_time", "id"],
           schema="task", predicate=OUTBOX_PENDING)
    _index("ix_runtime_result_outbox_lease", "runtime_result_outbox", ["lease_until"],
           schema="task", predicate=OUTBOX_LEASE)


def upgrade():
    _upgrade_existing()
    _create_tool_operation()
    _create_run_continuation()
    _create_tool_result_inbox()
    _create_control_outbox()
    _create_worker_tables()


def _check_drained():
    checks = (
        "SELECT EXISTS(SELECT 1 FROM runtime.run_record WHERE status = 'WAITING_TOOL')",
        "SELECT EXISTS(SELECT 1 FROM runtime.tool_control_outbox WHERE status = 'PENDING')",
        "SELECT EXISTS(SELECT 1 FROM task.runtime_result_outbox WHERE status = 'PENDING')",
        "SELECT EXISTS(SELECT 1 FROM runtime.tool_operation WHERE status NOT IN "
        "('COMPLETED','FAILED','CANCELLED','LATE'))",
        "SELECT EXISTS(SELECT 1 FROM runtime.tool_result_inbox WHERE NOT materialized AND NOT late)",
    )
    connection = op.get_bind()
    if any(connection.execute(sa.text(query)).scalar_one() for query in checks):
        raise RuntimeError("ASYNC_TOOL_DRAIN_REQUIRED: waiting Runs, operations and outboxes must be drained")


def downgrade():
    _check_drained()
    op.drop_table("runtime_result_outbox", schema="task")
    op.drop_table("runtime_operation", schema="task")
    op.drop_table("tool_control_outbox", schema="runtime")
    op.drop_table("tool_result_inbox", schema="runtime")
    op.drop_table("run_continuation", schema="runtime")
    op.drop_table("tool_operation", schema="runtime")
    op.drop_constraint("ck_task_execution_completion_mode", "task_execution", schema="task")
    for column in ("completion_mode", "source_tool_call_id", "source_operation_id"):
        op.drop_column("task_execution", column, schema="task")
    op.drop_index("uq_canonical_event_source", schema="runtime")
    op.drop_column("canonical_event", "source_event_id", schema="runtime")
    op.drop_index("ix_run_record_waiting_deadline", schema="runtime")
    op.drop_index("uq_run_record_active_conversation", schema="runtime")
    _index("uq_run_record_active_conversation", "run_record", ["conversation_id"], unique=True,
           predicate="status IN ('CREATED','RUNNING','WAITING_INPUT') AND is_deleted = false")
    op.drop_constraint("ck_run_record_run_status", "run_record", schema="runtime")
    op.drop_column("run_record", "execution_epoch", schema="runtime")
    op.drop_column("run_record", "deadline_at", schema="runtime")
