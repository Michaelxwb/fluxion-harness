import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from muad_contracts.enums import RunStatus
from pydantic import JsonValue
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
                "status IN ('CREATED', 'RUNNING', 'WAITING_TOOL', 'WAITING_INPUT') AND is_deleted = false"
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
        sa.Index(
            "ix_run_record_waiting_deadline",
            "deadline_at",
            postgresql_where=sa.text("status IN ('WAITING_TOOL','WAITING_INPUT') AND is_deleted = false"),
        ),
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
    status: Mapped[RunStatus] = mapped_column(
        sa.Enum(RunStatus, native_enum=False, create_constraint=True, name="run_status", length=24)
    )
    deadline_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    execution_epoch: Mapped[int] = mapped_column(sa.BigInteger(), server_default=sa.text("0"))
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
    channel_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


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
        sa.Index(
            "uq_canonical_event_source",
            "tenant_id",
            "source_event_id",
            unique=True,
            postgresql_where=sa.text("source_event_id IS NOT NULL AND is_deleted = false"),
        ),
        sa.Index(
            "uq_memory_consolidated_run",
            "tenant_id",
            "run_id",
            unique=True,
            postgresql_where=sa.text("event_type = 'MEMORY_CONSOLIDATED' AND is_deleted = false"),
        ),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("runtime.run_submission.id")
    )
    stream_type: Mapped[str | None] = mapped_column(sa.String(64))
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())


class RunInterrupt(StandardColumnsMixin, Base):
    __tablename__ = "run_interrupt"
    __table_args__ = (
        sa.Index("ix_run_interrupt_run_status", "run_id", "status"),
        sa.Index("ix_run_interrupt_conversation_status", "conversation_id", "status"),
        sa.Index(
            "uq_run_interrupt_pending_memory_confirm",
            "tenant_id",
            "run_id",
            unique=True,
            postgresql_where=sa.text(
                "interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING' AND is_deleted = false"
            ),
        ),
        sa.Index(
            "ix_memory_confirm_expiry",
            "tenant_id",
            "run_id",
            "expires_at",
            postgresql_where=sa.text("interrupt_type = 'MEMORY_CONFIRMATION' AND status = 'WAITING'"),
        ),
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
    proposal_json: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)


class RunSubmission(StandardColumnsMixin, Base):
    """Run 创建/resume 的持久化幂等记录（B-01/RULE-api-002）。"""

    __tablename__ = "run_submission"
    __table_args__ = (
        sa.Index(
            "uq_run_submission_tenant_key_endpoint",
            "tenant_id",
            "idempotency_key",
            "endpoint",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_run_submission_run_create_time", "run_id", "create_time"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), sa.ForeignKey("runtime.run_record.id"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("runtime.conversation.id")
    )
    response_status: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("200"))
    response_headers_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    first_seq: Mapped[int | None] = mapped_column(sa.BigInteger())
    last_seq: Mapped[int | None] = mapped_column(sa.BigInteger())
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default=sa.text("'OPEN'"))
    terminal_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class UserMemory(StandardColumnsMixin, Base):
    __tablename__ = "user_memory"
    __table_args__ = (
        sa.Index("uq_user_memory_key", "tenant_id", "user_id", "memory_key", unique=True),
        sa.Index(
            "ix_user_memory_candidates",
            "tenant_id",
            "user_id",
            "category",
            sa.text("update_time DESC"),
            postgresql_where=sa.text("is_deleted = false AND enabled = true"),
        ),
        sa.Index(
            "ix_user_memory_expiry",
            "tenant_id",
            "expires_at",
            "user_id",
            postgresql_where=sa.text("is_deleted = false AND expires_at IS NOT NULL"),
        ),
        sa.CheckConstraint("author IN ('USER','ADMIN','AGENT')", name="author"),
        sa.CheckConstraint(
            "category IN ('PREFERENCE','WORK_STYLE','EXPLICIT','TASK_STATE')", name="category"
        ),
        sa.CheckConstraint(
            "version >= 1 AND confidence BETWEEN 1 AND 5 AND confirmed_count >= 0", name="counters"
        ),
        sa.CheckConstraint(
            "author != 'AGENT' OR (confidence = 2 AND explicit_request = false AND expires_at IS NOT NULL)",
            name="agent_policy",
        ),
        sa.CheckConstraint(
            "author != 'USER' OR (explicit_request = true AND confirmation_event_id IS NOT NULL)",
            name="user_confirmation",
        ),
        sa.CheckConstraint("author != 'ADMIN' OR explicit_request = false", name="admin_confirmation"),
        sa.CheckConstraint("write_policy = 'SERVER_CONTROLLED'", name="write_policy"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    memory_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    category: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    author: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default=sa.text("'AGENT'"))
    explicit_request: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.text("false")
    )
    confidence: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False, server_default=sa.text("2"))
    confirmed_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    last_confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    source_seq: Mapped[int | None] = mapped_column(sa.BigInteger())
    source_ref: Mapped[str | None] = mapped_column(sa.String(256))
    source_quote: Mapped[str | None] = mapped_column(sa.String(512))
    confirmation_event_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    retirement_reason: Mapped[str | None] = mapped_column(sa.String(32))
    retirement_revision: Mapped[int | None] = mapped_column(sa.BigInteger())
    last_source_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    last_source_seq: Mapped[int | None] = mapped_column(sa.BigInteger())
    write_policy: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default=sa.text("'SERVER_CONTROLLED'")
    )
    version: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("1"))
    enabled: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, server_default=sa.text("true"))


class MemoryScope(StandardColumnsMixin, Base):
    __tablename__ = "memory_scope"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memory_scope_user"),
        sa.CheckConstraint("is_deleted = false", name="not_deleted"),
        sa.CheckConstraint("revision >= 0 AND forget_epoch >= 0", name="revision"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    revision: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    forget_epoch: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))


class UserMemoryHistory(StandardColumnsMixin, Base):
    __tablename__ = "user_memory_history"
    __table_args__ = (
        sa.UniqueConstraint("origin_id", "version", name="uq_user_memory_history_origin_version"),
        sa.Index(
            "ix_user_memory_history_key_version",
            "tenant_id",
            "user_id",
            "memory_key",
            sa.text("version DESC"),
        ),
        sa.CheckConstraint("is_deleted = false", name="not_deleted"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    memory_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    origin_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("runtime.user_memory.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    change_reason: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(sa.String(128))
    command_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    after_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    legacy_snapshots_json: Mapped[list[dict[str, JsonValue]] | None] = mapped_column(JSONB)


class MemoryCommandReceipt(StandardColumnsMixin, Base):
    __tablename__ = "memory_command_receipt"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "command_id", name="uq_memory_command_receipt_command"),
        sa.CheckConstraint("is_deleted = false", name="not_deleted"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    command_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    response_json: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())


class MemoryConsolidationJob(StandardColumnsMixin, Base):
    __tablename__ = "memory_consolidation_job"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "run_id", name="uq_memory_job_run"),
        sa.Index(
            "ix_memory_job_claim",
            "tenant_id",
            "next_attempt_at",
            "create_time",
            postgresql_where=sa.text("status IN ('PENDING','RETRY')"),
        ),
        sa.Index(
            "ix_memory_job_lease", "tenant_id", "lease_until", postgresql_where=sa.text("status = 'RUNNING'")
        ),
        sa.CheckConstraint("is_deleted = false", name="not_deleted"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','RETRY','DONE','SKIPPED','FAILED')", name="status"
        ),
        sa.CheckConstraint("claim_epoch >= 0 AND attempt >= 0", name="counters"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    through_seq: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default=sa.text("'PENDING'"))
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128))
    claim_epoch: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False, server_default=sa.text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    result_json: Mapped[list[dict[str, JsonValue]] | None] = mapped_column(JSONB)
    enqueued_scope_revision: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    input_scope_revision: Mapped[int | None] = mapped_column(sa.BigInteger())
    input_json: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    input_hash: Mapped[str | None] = mapped_column(sa.String(128))
    resume_ref_json: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(sa.String(64))


class Artifact(StandardColumnsMixin, Base):
    __tablename__ = "artifact"
    __table_args__ = (
        sa.CheckConstraint(
            "(run_id IS NOT NULL) <> (task_id IS NOT NULL)",
            name="ck_artifact_run_task_xor",
        ),
        sa.Index("ix_artifact_run", "run_id"),
        sa.Index("ix_artifact_conversation", "conversation_id"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("runtime.run_record.id"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(), nullable=True)
    artifact_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    media_type: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    size: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    checksum: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    preview_text: Mapped[str | None] = mapped_column(sa.Text())
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )


class ToolCallAudit(StandardColumnsMixin, Base):
    __tablename__ = "tool_call_audit"
    __table_args__ = (
        sa.Index("ix_tool_call_audit_run", "run_id", "create_time"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    conversation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    tool_kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    prepared_args_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    args_preview_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    start_time: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer())
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.Text())
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())


class EgressAudit(StandardColumnsMixin, Base):
    __tablename__ = "egress_audit"
    __table_args__ = (
        sa.Index("ix_egress_audit_run", "run_id", "create_time"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    skill_artifact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    platform_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    adapter_key: Mapped[str | None] = mapped_column(sa.String(128))
    target_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    target: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    operation: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    method: Mapped[str | None] = mapped_column(sa.String(16))
    policy_decision: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    credential_ref_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    status_code: Mapped[int | None] = mapped_column(sa.Integer())
    result_status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer())
    error_code: Mapped[str | None] = mapped_column(sa.String(64))


class ModelInvocationAudit(StandardColumnsMixin, Base):
    __tablename__ = "model_invocation_audit"
    __table_args__ = (
        sa.Index("ix_model_invocation_audit_run", "run_id", "attempt"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    provider: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    attempt: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    retry_reason: Mapped[str | None] = mapped_column(sa.String(64))
    input_tokens: Mapped[int | None] = mapped_column(sa.Integer())
    output_tokens: Mapped[int | None] = mapped_column(sa.Integer())
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer())
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(sa.String(64))


class ModelCapacity(StandardColumnsMixin, Base):
    """实际 Key / 额度组的计量身份（容量以 PG 预占为权威）。

    完整唯一含 tombstone：轮换或删除重录不重置旧计量，同一实际 Key 在所有池中
    合并为一行；窗口与调度计数分开，`window_start` 只表示当前额度窗口起点。
    """

    __tablename__ = "model_capacity"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "resource_type", "resource_id", name="uq_model_capacity_resource"),
        sa.CheckConstraint("resource_type IN ('KEY','QUOTA_GROUP')", name="resource_type"),
        sa.CheckConstraint("capacity_revision >= 1", name="capacity_revision"),
        sa.CheckConstraint("inflight >= 0", name="inflight"),
        sa.CheckConstraint("reserved_tokens >= 0", name="reserved_tokens"),
        sa.CheckConstraint("reserved_requests >= 0", name="reserved_requests"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    capacity_revision: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, server_default=sa.text("1")
    )
    inflight: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("0"))
    window_start: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    reserved_tokens: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    reserved_requests: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))


class ModelDispatchState(StandardColumnsMixin, Base):
    """按冻结策略版本（policy_hash）分别解释的调度计数；池锁内更新，不承载物理额度。"""

    __tablename__ = "model_dispatch_state"
    __table_args__ = (
        sa.UniqueConstraint(
            "tenant_id", "model_id", "deployment_id", "policy_hash", name="uq_model_dispatch_state_policy"
        ),
        sa.CheckConstraint("dispatch_epoch >= 0", name="dispatch_epoch"),
        sa.CheckConstraint("dispatch_count >= 0", name="dispatch_count"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    policy_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    dispatch_epoch: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    dispatch_count: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    last_picked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ModelCallLease(StandardColumnsMixin, Base):
    """单次模型调用尝试的容量租约：冻结实际 Key 身份，UNKNOWN 保留占用。

    一次 call_attempt 恰好归属 run/memory_job/probe 之一（同 Owner 物理 FK，probe 逻辑
    UUID）；`is_deleted = false` 保证 UNKNOWN 隔离证据不能被软删除隐藏。
    """

    __tablename__ = "model_call_lease"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "call_attempt_id", name="uq_model_call_lease_attempt"),
        sa.Index(
            "ix_model_call_lease_active",
            "tenant_id",
            "key_resource_id",
            postgresql_where=sa.text("state IN ('ACTIVE','UNKNOWN') AND is_deleted = false"),
        ),
        sa.CheckConstraint("num_nonnulls(run_id, memory_job_id, probe_id) = 1", name="owner"),
        sa.CheckConstraint("state IN ('ACTIVE','UNKNOWN','SETTLED')", name="state"),
        sa.CheckConstraint("workload IN ('INTERACTIVE','BACKGROUND','PROBE')", name="workload"),
        sa.CheckConstraint("credential_revision >= 1", name="credential_revision"),
        sa.CheckConstraint("epoch >= 1", name="epoch"),
        sa.CheckConstraint("reserved_tokens >= 0", name="reserved_tokens"),
        sa.CheckConstraint("is_deleted = false", name="not_deleted"),
        {"schema": "runtime"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    call_attempt_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("runtime.run_record.id")
    )
    memory_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("runtime.memory_consolidation_job.id")
    )
    probe_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    deployment_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    key_resource_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    quota_group_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid())
    credential_revision: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    workload: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    lease_owner: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    epoch: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False, server_default=sa.text("1"))
    lease_until: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'"))
