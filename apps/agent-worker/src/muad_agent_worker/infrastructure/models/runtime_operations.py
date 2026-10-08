"""Worker-owned admission/cancellation identity and durable terminal outbox."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from muad_contracts.enums import CompletionMode, ControlOutboxStatus
from pydantic import JsonValue
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class RuntimeOperation(StandardColumnsMixin, Base):
    __tablename__ = "runtime_operation"
    __table_args__ = (
        sa.Index(
            "uq_runtime_operation_identity",
            "tenant_id",
            "operation_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    source_tool_call_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("task.task_execution.id"))
    cancel_requested: Mapped[bool] = mapped_column(server_default=sa.text("false"))
    submission_hash: Mapped[str | None] = mapped_column(sa.String(128))
    completion_mode: Mapped[CompletionMode | None] = mapped_column(
        sa.Enum(CompletionMode, native_enum=False, create_constraint=True, name="completion_mode")
    )


class RuntimeResultOutbox(StandardColumnsMixin, Base):
    __tablename__ = "runtime_result_outbox"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["task_id", "task_event_seq"], ["task.task_event.task_id", "task.task_event.seq"]
        ),
        sa.Index(
            "uq_runtime_result_outbox_event",
            "tenant_id",
            "event_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_runtime_result_outbox_terminal",
            "task_id",
            "task_event_seq",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "ix_runtime_result_outbox_pending",
            "not_before",
            "create_time",
            "id",
            postgresql_where=sa.text("status = 'PENDING' AND is_deleted = false"),
        ),
        sa.Index(
            "ix_runtime_result_outbox_lease",
            "lease_until",
            postgresql_where=sa.text("status = 'PENDING' AND lease_until IS NOT NULL AND is_deleted = false"),
        ),
        sa.CheckConstraint("attempts >= 0 AND task_event_seq > 0", name="counters"),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("task.task_execution.id"), nullable=False)
    task_event_seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_snapshot_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    payload_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[ControlOutboxStatus] = mapped_column(
        sa.Enum(ControlOutboxStatus, native_enum=False, create_constraint=True, name="outbox_status"),
        server_default=sa.text("'PENDING'"),
    )
    attempts: Mapped[int] = mapped_column(server_default=sa.text("0"))
    not_before: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64))
