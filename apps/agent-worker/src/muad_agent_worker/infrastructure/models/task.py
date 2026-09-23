import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class DeliveryRoute(StandardColumnsMixin, Base):
    __tablename__ = "delivery_route"
    __table_args__ = (
        sa.Index(
            "uq_delivery_route_route_hash",
            "route_hash",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "ix_delivery_route_platform_user_channel_update_time",
            "platform_user_id",
            "channel",
            sa.text("update_time DESC"),
        ),
        sa.Index("ix_delivery_route_bot_id_external_user_id", "bot_id", "external_user_id"),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    channel: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    bot_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    platform_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    external_user_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    external_conversation_id: Mapped[str | None] = mapped_column(sa.String(256))
    route_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    route_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )


class TaskSchedule(StandardColumnsMixin, Base):
    __tablename__ = "task_schedule"
    __table_args__ = (
        sa.Index("ix_task_schedule_status_next_fire_at", "status", "next_fire_at"),
        sa.Index("ix_task_schedule_actor_user_status", "actor_user_id", "status"),
        sa.Index("ix_task_schedule_agent_status", "agent_id", "status"),
        sa.CheckConstraint(
            "(schedule_type = 'CRON' AND cron_expr IS NOT NULL) "
            "OR (schedule_type = 'ONCE' AND run_at IS NOT NULL)",
            name="trigger",
        ),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    intent_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    skill_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    input_template_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schedule_type: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'CRON'"),
    )
    cron_expr: Mapped[str | None] = mapped_column(sa.String(128))
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    delivery_route_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("task.delivery_route.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
    next_fire_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_fire_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(
        sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64))
    last_error_message: Mapped[str | None] = mapped_column(sa.Text())
    last_skipped_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class TaskExecution(StandardColumnsMixin, Base):
    __tablename__ = "task_execution"
    __table_args__ = (
        sa.Index(
            "uq_task_execution_tenant_idempotency_key",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_task_execution_parent_item_key",
            "parent_id",
            "item_key",
            unique=True,
            postgresql_where=sa.text("parent_id IS NOT NULL AND is_deleted = false"),
        ),
        sa.Index(
            "ix_task_execution_status_not_before_priority_create_time",
            "status",
            "not_before",
            "priority",
            "create_time",
        ),
        sa.Index(
            "ix_task_execution_running_lease_until",
            "lease_until",
            postgresql_where=sa.text("status = 'RUNNING'"),
        ),
        sa.Index("ix_task_execution_root_parent_status", "root_id", "parent_id", "status"),
        sa.Index("ix_task_execution_actor_user_create_time", "actor_user_id", sa.text("create_time DESC")),
        sa.Index("ix_task_execution_schedule_create_time", "schedule_id", sa.text("create_time DESC")),
        sa.Index(
            "ix_task_execution_final_delivery",
            "delivery_status",
            "delivered_at",
            postgresql_where=sa.text("delivery_mode = 'FINAL_ONLY' AND is_deleted = false"),
        ),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("task.task_execution.id"),
    )
    root_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    item_key: Mapped[str | None] = mapped_column(sa.String(128))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("task.task_schedule.id"),
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    intent_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    skill_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    skill_artifact_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    trigger_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    execution_mode: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    task_type: Mapped[str] = mapped_column(
        sa.String(24),
        nullable=False,
        server_default=sa.text("'SKILL'"),
    )
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text())
    cancel_requested: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )
    external_ref_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    deadline_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '24 hours'"),
    )
    execution_snapshot_schema_version: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )
    execution_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    priority: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("100"),
    )
    attempt: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("3"),
    )
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    delivery_route_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("task.delivery_route.id"),
    )
    delivery_mode: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'FINAL_ONLY'"),
    )
    delivery_status: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'PENDING'"),
    )
    delivery_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    delivery_attempts: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class TaskEvent(StandardColumnsMixin, Base):
    __tablename__ = "task_event"
    __table_args__ = (
        sa.UniqueConstraint("task_id", "seq", name="uq_task_event_task_seq"),
        sa.Index("ix_task_event_task_create_time", "task_id", "create_time"),
        sa.Index("ix_task_event_event_type_create_time", "event_type", sa.text("create_time DESC")),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))
