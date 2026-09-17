import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class Conversation(StandardColumnsMixin, Base):
    __tablename__ = "conversation"
    __table_args__ = (
        sa.Index(
            "ix_conversation_tenant_user_agent_update_time",
            "tenant_id",
            "user_id",
            "agent_id",
            sa.text("update_time DESC"),
        ),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.String(256))
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'"))
    last_seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())


class RunRecord(StandardColumnsMixin, Base):
    __tablename__ = "run_record"
    __table_args__ = (
        sa.Index(
            "uq_run_record_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('CREATED', 'RUNNING', 'WAITING_INPUT') AND is_deleted = false"
            ),
        ),
        sa.Index("ix_run_record_conversation_create_time", "conversation_id", sa.text("create_time DESC")),
        sa.Index("ix_run_record_agent_status_create_time", "agent_id", "status", sa.text("create_time DESC")),
        sa.Index(
            "ix_run_record_running_lease_until",
            "lease_until",
            postgresql_where=sa.text("status = 'RUNNING'"),
        ),
        sa.Index("ix_run_record_trace_id", "trace_id"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("runtime.conversation.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    input_text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    trace_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    start_time: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text())
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class RuntimeSnapshot(StandardColumnsMixin, Base):
    __tablename__ = "runtime_snapshot"
    __table_args__ = (
        sa.UniqueConstraint("run_id", name="uq_runtime_snapshot_run_id"),
        sa.Index("ix_runtime_snapshot_content_hash", "content_hash"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    schema_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    agent_revision: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    model_revision: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    agent_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    skill_catalog_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    mcp_catalog_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    policy_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    prompt_template_version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)


class CanonicalEvent(StandardColumnsMixin, Base):
    __tablename__ = "canonical_event"
    __table_args__ = (
        sa.UniqueConstraint("conversation_id", "seq", name="uq_canonical_event_conversation_seq"),
        sa.Index("ix_canonical_event_run_seq", "run_id", "seq"),
        sa.Index("ix_canonical_event_conversation_create_time", "conversation_id", "create_time"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())


class RunInterrupt(StandardColumnsMixin, Base):
    __tablename__ = "run_interrupt"
    __table_args__ = (
        sa.Index("ix_run_interrupt_run_status", "run_id", "status"),
        sa.Index("ix_run_interrupt_conversation_status", "conversation_id", "status"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    interrupt_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    prompt_text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    options_json: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default=sa.text("'WAITING'"))
    resolution_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
