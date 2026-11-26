"""`task` schema 的调度模型（scheduled-task-v2 §3.3）。

一条事实一个落点：Schedule 只存身份与状态，配置在不可变 Revision，一次 occurrence 由 Fire
记账，真实执行是 Execution；Delivery 与 Human 各自独立，不塞回 Execution 行。
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from muad_contracts.enums import CompletionMode
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin

SCHEDULE = "task"


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
    """Schedule 身份与生命周期；配置全在 `task_schedule_revision`。"""

    __tablename__ = "task_schedule"
    __table_args__ = (
        sa.Index(
            "ix_task_schedule_due",
            "status",
            "next_fire_at",
            postgresql_where=sa.text("status = 'ACTIVE' AND is_deleted = false"),
        ),
        sa.Index(
            "ix_task_schedule_owner",
            "tenant_id",
            "owner_user_id",
            "status",
            sa.text("update_time DESC"),
        ),
        sa.Index("ix_task_schedule_agent", "agent_id", "status"),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(24),
        nullable=False,
        server_default=sa.text("'ACTIVE'"),
    )
    #: 循环 FK：创建事务内回填，ACTIVE 时由应用保证非空。
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey(
            "task.task_schedule_revision.id",
            name="fk_task_schedule_current_revision",
            use_alter=True,
        ),
    )
    next_fire_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_fire_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    occurrence_count: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, server_default=sa.text("0")
    )
    config_version: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, server_default=sa.text("1")
    )
    attention_reason: Mapped[str | None] = mapped_column(sa.String(64))
    attention_since: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(sa.String(32))
    #: 有界的最近一次 meaningful result 与 automation scratch（state，不是配置）。
    last_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scratch_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    scratch_revision: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, server_default=sa.text("0")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)


class TaskScheduleRevision(StandardColumnsMixin, Base):
    """不可变配置；纠错靠新 Revision，不改旧行。"""

    __tablename__ = "task_schedule_revision"
    __table_args__ = (
        sa.UniqueConstraint("schedule_id", "revision_no", name="uq_task_schedule_revision_no"),
        sa.Index("ix_task_schedule_revision_schedule", "schedule_id", sa.text("revision_no DESC")),
        sa.CheckConstraint(
            "(recurrence_kind = 'CRON' AND cron_expr IS NOT NULL AND run_at IS NULL) "
            "OR (recurrence_kind = 'ONCE' AND run_at IS NOT NULL AND cron_expr IS NULL)",
            name="recurrence",
        ),
        sa.CheckConstraint(
            "delivery_intent = 'NONE' OR delivery_route_id IS NOT NULL",
            name="delivery_route",
        ),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_schedule.id"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    mode: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    instruction_raw: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    normalized_goal: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    recurrence_kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    cron_expr: Mapped[str | None] = mapped_column(sa.String(128))
    run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    schedule_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    context_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    #: `mode` 是内部派生字段；用户/模型只写 payload。
    payload_kind: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'REMINDER'")
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    session_target: Mapped[str] = mapped_column(
        sa.String(128), nullable=False, server_default=sa.text("'isolated'")
    )
    tools_policy_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    delivery_intent: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'NONE'")
    )
    delivery_route_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.delivery_route.id")
    )
    preflight_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)


class TaskScheduleDraft(StandardColumnsMixin, Base):
    """用户确认前唯一的配置载体；确认后不可修改。"""

    __tablename__ = "task_schedule_draft"
    __table_args__ = (
        sa.Index(
            "uq_task_schedule_draft_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index(
            "uq_task_schedule_draft_confirmation",
            "tenant_id",
            "confirmation_surface",
            "confirmation_ref",
            unique=True,
            postgresql_where=sa.text("status = 'CONFIRMED' AND is_deleted = false"),
        ),
        sa.Index(
            "ix_task_schedule_draft_owner_status",
            "tenant_id",
            "owner_user_id",
            "status",
            "expires_at",
        ),
        sa.CheckConstraint("draft_hash ~ '^sha256:[0-9a-f]{64}$'", name="draft_hash"),
        sa.CheckConstraint(
            "(kind = 'UPDATE' AND base_schedule_id IS NOT NULL AND base_config_version IS NOT NULL) "
            "OR (kind = 'CREATE' AND base_schedule_id IS NULL AND base_config_version IS NULL)",
            name="kind_shape",
        ),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    base_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_schedule.id")
    )
    base_config_version: Mapped[int | None] = mapped_column(sa.BigInteger())
    draft_hash: Mapped[str] = mapped_column(sa.String(71), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    preview_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    confirmation_surface: Mapped[str | None] = mapped_column(sa.String(16))
    confirmation_ref: Mapped[str | None] = mapped_column(sa.String(256))
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_schedule.id")
    )
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)


class ScheduleFire(StandardColumnsMixin, Base):
    """一次 occurrence 的台账；revision 是属性不是身份，Fire 不持有第二套 claim。"""

    __tablename__ = "schedule_fire"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "schedule_id", "scheduled_at", name="uq_schedule_fire_occurrence"
        ),
        sa.Index(
            "uq_schedule_fire_execution",
            "execution_id",
            unique=True,
            postgresql_where=sa.text("execution_id IS NOT NULL"),
        ),
        sa.Index("ix_schedule_fire_status_scheduled", "status", "scheduled_at"),
        sa.Index("ix_schedule_fire_tenant_create", "tenant_id", sa.text("create_time DESC")),
        sa.Index("ix_schedule_fire_schedule_scheduled", "schedule_id", sa.text("scheduled_at DESC")),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_schedule.id"), nullable=False
    )
    schedule_revision_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_schedule_revision.id"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey(
            "task.task_execution.id",
            name="fk_schedule_fire_execution",
            use_alter=True,
        ),
    )
    skip_reason: Mapped[str | None] = mapped_column(sa.String(32))
    missed_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    dst_resolution: Mapped[str | None] = mapped_column(sa.String(24))
    admission_error_code: Mapped[str | None] = mapped_column(sa.String(64))


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
            "uq_task_execution_root_fire",
            "schedule_fire_id",
            unique=True,
            postgresql_where=sa.text("schedule_fire_id IS NOT NULL AND parent_id IS NULL"),
        ),
        sa.Index(
            "ix_task_execution_status_not_before_priority_create_time",
            "status",
            "not_before",
            "priority",
            "create_time",
        ),
        sa.Index(
            "ix_task_execution_claim_scheduled",
            "status",
            "scheduled_at",
            postgresql_where=sa.text("scheduled_at IS NOT NULL"),
        ),
        sa.Index(
            "ix_task_execution_running_lease_until",
            "lease_until",
            postgresql_where=sa.text("status = 'RUNNING'"),
        ),
        sa.Index("ix_task_execution_root_parent_status", "root_id", "parent_id", "status"),
        sa.Index(
            "ix_task_execution_source_run",
            "tenant_id",
            "source_run_id",
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_task_execution_actor_user_create_time", "actor_user_id", sa.text("create_time DESC")),
        sa.Index("ix_task_execution_schedule_status_scheduled", "schedule_id", "status", "scheduled_at"),
        sa.Index("ix_task_execution_schedule_create_time", "schedule_id", sa.text("create_time DESC")),
        sa.Index(
            "ix_task_execution_waiting_external_sweep",
            "wait_kind",
            "not_before",
            postgresql_where=sa.text("status = 'WAITING_EXTERNAL'"),
        ),
        sa.CheckConstraint("lease_epoch >= 0", name="lease_epoch"),
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
    schedule_fire_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey(
            "task.schedule_fire.id",
            name="fk_task_execution_schedule_fire",
            use_alter=True,
        ),
    )
    schedule_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_schedule_revision.id")
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    source_operation_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    source_tool_call_id: Mapped[str | None] = mapped_column(sa.String(256))
    completion_mode: Mapped[CompletionMode | None] = mapped_column(
        sa.Enum(CompletionMode, native_enum=False, create_constraint=True, name="completion_mode"))
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_type: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=sa.text("'SKILL'"),
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    skill_artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    trigger_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    skill_execution_mode: Mapped[str | None] = mapped_column(sa.String(16))
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    wait_kind: Mapped[str | None] = mapped_column(sa.String(24))
    wait_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text())
    failure_class: Mapped[str | None] = mapped_column(sa.String(24))
    cancel_requested: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )
    deadline_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '24 hours'"),
    )
    execution_snapshot_schema_version: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("4"),
    )
    execution_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    snapshot_bytes: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
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
    #: 每次领取均递增（含等待续接）；attempt 仍只统计业务重试。
    lease_epoch: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    #: 只在首次开始执行时写一次；reclaim/续接不得清空（判断“未开始”用 IS NULL）。
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    resume_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    last_resume_reason: Mapped[str | None] = mapped_column(sa.String(16))
    last_checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())


class TaskExecutionCheckpoint(StandardColumnsMixin, Base):
    __tablename__ = "task_execution_checkpoint"
    __table_args__ = (
        sa.UniqueConstraint("execution_id", "checkpoint_no", name="uq_task_execution_checkpoint_no"),
        sa.Index("ix_task_execution_checkpoint_exec", "execution_id", sa.text("checkpoint_no DESC")),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False
    )
    checkpoint_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class TaskHumanInteraction(StandardColumnsMixin, Base):
    __tablename__ = "task_human_interaction"
    __table_args__ = (
        sa.Index(
            "uq_task_human_interaction_open",
            "execution_id",
            unique=True,
            postgresql_where=sa.text("status = 'OPEN' AND is_deleted = false"),
        ),
        sa.Index("ix_task_human_interaction_deadline", "status", "deadline_at"),
        sa.Index(
            "ix_task_human_interaction_owner_deadline",
            "tenant_id",
            "owner_user_id",
            "status",
            "deadline_at",
        ),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    interaction_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    question: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivery_route_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.delivery_route.id")
    )
    source_message_id: Mapped[str | None] = mapped_column(sa.String(256))
    deadline_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class TaskDelivery(StandardColumnsMixin, Base):
    __tablename__ = "task_delivery"
    __table_args__ = (
        sa.Index(
            "uq_task_delivery_key",
            "tenant_id",
            "delivery_key",
            "delivery_route_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_task_delivery_status_update_time", "status", "update_time"),
        sa.Index("ix_task_delivery_execution", "execution_id"),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False
    )
    delivery_route_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.delivery_route.id"), nullable=False
    )
    delivery_kind: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    delivery_key: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'PENDING'")
    )
    attempt_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64))
    last_error_message: Mapped[str | None] = mapped_column(sa.Text())
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class TaskDeliveryAttempt(StandardColumnsMixin, Base):
    __tablename__ = "task_delivery_attempt"
    __table_args__ = (
        sa.UniqueConstraint("delivery_id", "attempt_no", name="uq_task_delivery_attempt_no"),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_delivery.id"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    request_ref: Mapped[str | None] = mapped_column(sa.String(256))
    response_ref: Mapped[str | None] = mapped_column(sa.String(256))
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class TaskMessageOutbox(StandardColumnsMixin, Base):
    """Task 成功终态的消息级发件箱（design §4.6）。

    终态事务只写这一行，发送由 Worker 消费者交给 Runtime `message_delivery`。稳定键与
    `runtime.message_delivery` 同口径：`message_key` 跨重试稳定，`target_key` 是渠道中立的
    路由身份；二者加租户/任务构成幂等事实，重放不新增。
    """

    __tablename__ = "message_outbox"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "message_key",
            "target_key",
            name="uq_task_message_outbox_identity",
        ),
        sa.Index(
            "ix_task_message_outbox_due",
            "tenant_id",
            "next_attempt_at",
            postgresql_where=sa.text("status = 'PENDING'"),
        ),
        sa.Index("ix_task_message_outbox_task", "task_id"),
        sa.CheckConstraint("status IN ('PENDING','APPLIED','FAILED')", name="status"),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_execution.id"), nullable=False
    )
    message_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    target_key: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    route_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'PENDING'")
    )
    attempts: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64))


class TaskEvent(StandardColumnsMixin, Base):
    """多态审计事件：`entity_type + entity_id` 定位主体，不建物理 FK。"""

    __tablename__ = "task_event"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "entity_type", "entity_id", "seq", name="uq_task_event_entity_seq"
        ),
        sa.Index("ix_task_event_entity_create_time", "entity_type", "entity_id", "create_time"),
        sa.Index("ix_task_event_event_type_create_time", "event_type", sa.text("create_time DESC")),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    trace_id: Mapped[str | None] = mapped_column(sa.String(64))
