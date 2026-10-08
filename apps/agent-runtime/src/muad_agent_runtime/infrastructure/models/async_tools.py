"""Durable async-tool identities, checkpoints, receipts and control commands."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from muad_contracts.enums import (
    CompletionMode,
    ControlCommand,
    ControlOutboxStatus,
    OperationErrorPhase,
    OperationStatus,
)
from pydantic import JsonValue
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class ToolOperation(StandardColumnsMixin, Base):
    __tablename__ = "tool_operation"
    __table_args__ = (
        sa.Index(
            "uq_tool_operation_run_call",
            "tenant_id",
            "run_id",
            "source_tool_call_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_tool_operation_task",
            "tenant_id",
            "task_id",
            unique=True,
            postgresql_where=sa.text("task_id IS NOT NULL AND is_deleted = false"),
        ),
        sa.Index(
            "ix_tool_operation_pending",
            "tenant_id",
            "run_id",
            "status",
            "completion_mode",
            postgresql_where=sa.text(
                "status IN ('SUBMIT_PENDING','SUBMITTED','TASK_ACCEPTED',"
                "'RUNNING','RESULT_RECEIVED','MATERIALIZED') AND is_deleted = false"
            ),
        ),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("runtime.run_record.id"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    source_tool_call_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    completion_mode: Mapped[CompletionMode] = mapped_column(
        sa.Enum(CompletionMode, native_enum=False, create_constraint=True, name="completion_mode")
    )
    status: Mapped[OperationStatus] = mapped_column(
        sa.Enum(OperationStatus, native_enum=False, create_constraint=True, name="operation_status"),
        server_default=sa.text("'SUBMIT_PENDING'"),
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    task_snapshot_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    submission_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    terminal_event_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    cancel_requested: Mapped[bool] = mapped_column(server_default=sa.text("false"))
    error_phase: Mapped[OperationErrorPhase | None] = mapped_column(
        sa.Enum(OperationErrorPhase, native_enum=False, create_constraint=True, name="error_phase")
    )
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class RunContinuation(StandardColumnsMixin, Base):
    __tablename__ = "run_continuation"
    __table_args__ = (
        sa.Index(
            "uq_run_continuation_run", "run_id", unique=True, postgresql_where=sa.text("is_deleted = false")
        ),
        sa.Index(
            "ix_run_continuation_ready",
            "run_id",
            postgresql_where=sa.text("ready = true AND is_deleted = false"),
        ),
        sa.CheckConstraint("phase = 'BEFORE_MODEL'", name="phase"),
        sa.CheckConstraint(
            "wait_generation >= 0 AND context_upto_seq >= 0 AND consumed_event_seq >= 0 "
            "AND turns >= 0 AND tool_calls >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
            name="counters",
        ),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("runtime.run_record.id"), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("runtime.runtime_snapshot.id"), nullable=False
    )
    phase: Mapped[str] = mapped_column(sa.String(24), server_default=sa.text("'BEFORE_MODEL'"))
    wait_generation: Mapped[int] = mapped_column(sa.BigInteger(), server_default=sa.text("0"))
    ready: Mapped[bool] = mapped_column(server_default=sa.text("false"))
    context_upto_seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    consumed_event_seq: Mapped[int] = mapped_column(sa.BigInteger(), server_default=sa.text("0"))
    turns: Mapped[int] = mapped_column(server_default=sa.text("0"))
    tool_calls: Mapped[int] = mapped_column(server_default=sa.text("0"))
    input_tokens: Mapped[int | None] = mapped_column(sa.BigInteger())
    output_tokens: Mapped[int | None] = mapped_column(sa.BigInteger())
    checkpoint_schema_version: Mapped[int] = mapped_column(server_default=sa.text("1"))
    runner_state_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)


class ToolResultInbox(StandardColumnsMixin, Base):
    __tablename__ = "tool_result_inbox"
    __table_args__ = (
        sa.Index(
            "uq_tool_result_inbox_event",
            "tenant_id",
            "event_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "ix_tool_result_inbox_ready",
            "tenant_id",
            "run_id",
            "receipt_seq",
            postgresql_where=sa.text("materialized = false AND late = false AND is_deleted = false"),
        ),
        sa.CheckConstraint("task_event_seq > 0 AND receipt_seq > 0", name="sequences"),
        sa.CheckConstraint("NOT materialized OR canonical_event_id IS NOT NULL", name="materialized"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("runtime.run_record.id"), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("runtime.tool_operation.id"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_event_seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    payload_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    late: Mapped[bool] = mapped_column(server_default=sa.text("false"))
    receipt_event_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("runtime.canonical_event.id"), nullable=False
    )
    receipt_seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    canonical_event_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("runtime.canonical_event.id"))
    materialized: Mapped[bool] = mapped_column(server_default=sa.text("false"))


class ToolControlOutbox(StandardColumnsMixin, Base):
    __tablename__ = "tool_control_outbox"
    __table_args__ = (
        sa.Index(
            "uq_tool_control_outbox_command",
            "operation_id",
            "command",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "ix_tool_control_outbox_pending",
            "not_before",
            "create_time",
            "id",
            postgresql_where=sa.text("status = 'PENDING' AND is_deleted = false"),
        ),
        sa.Index(
            "ix_tool_control_outbox_lease",
            "lease_until",
            postgresql_where=sa.text("status = 'PENDING' AND lease_until IS NOT NULL AND is_deleted = false"),
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("runtime.tool_operation.id"), nullable=False
    )
    command: Mapped[ControlCommand] = mapped_column(
        sa.Enum(ControlCommand, native_enum=False, create_constraint=True, name="command")
    )
    status: Mapped[ControlOutboxStatus] = mapped_column(
        sa.Enum(ControlOutboxStatus, native_enum=False, create_constraint=True, name="outbox_status"),
        server_default=sa.text("'PENDING'"),
    )
    attempts: Mapped[int] = mapped_column(server_default=sa.text("0"))
    not_before: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64))
