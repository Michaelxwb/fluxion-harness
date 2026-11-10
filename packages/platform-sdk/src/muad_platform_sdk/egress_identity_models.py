"""Shared SDK models for Runtime-owned persistent egress identities.

Runtime and Worker consume the same package; no application imports are needed.
The registry is not a request counter. Migrations own DDL creation.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class IdentityBase(DeclarativeBase):
    pass


class IdentityColumns:
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True,
                                        server_default=sa.text("gen_random_uuid()"))
    is_deleted: Mapped[bool] = mapped_column(server_default=sa.text("false"))
    create_time: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                 server_default=sa.text("now()"))
    update_time: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True),
                                                 server_default=sa.text("now()"))
    tenant_id: Mapped[str] = mapped_column(sa.String(64))
    state: Mapped[str] = mapped_column(sa.String(32), server_default=sa.text("'NEW'"))
    init_token_hash: Mapped[str | None] = mapped_column(sa.String(64))


class EgressExecution(IdentityColumns, IdentityBase):
    __tablename__ = "egress_execution"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "source_kind", "source_id", name="uq_egress_execution_source"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_egress_execution_tenant_id"),
        sa.CheckConstraint("source_kind IN ('RUN','TASK')", name="ck_egress_execution_source_kind"),
        sa.CheckConstraint("state IN ('NEW','INITIALIZING','READY','BLOCKED','TERMINAL')",
                           name="ck_egress_execution_state"),
        sa.CheckConstraint("current_fence >= 0", name="ck_egress_execution_fence"),
        sa.Index("ix_egress_execution_state_time", "state", "update_time"),
        {"schema": "runtime"},
    )
    source_kind: Mapped[str] = mapped_column(sa.String(8))
    source_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid())
    policy_json: Mapped[dict[str, object]] = mapped_column(JSONB())
    policy_hash: Mapped[str] = mapped_column(sa.String(64))
    meter_epoch: Mapped[str] = mapped_column(sa.String(128))
    current_fence: Mapped[int] = mapped_column(sa.BigInteger(), server_default=sa.text("0"))


class EgressInvocation(IdentityColumns, IdentityBase):
    __tablename__ = "egress_invocation"
    __table_args__ = (
        sa.UniqueConstraint("execution_id", "operation_key", name="uq_egress_invocation_operation"),
        sa.ForeignKeyConstraint(["tenant_id", "execution_id"],
                                ["runtime.egress_execution.tenant_id", "runtime.egress_execution.id"],
                                name="fk_egress_invocation_tenant_execution"),
        sa.CheckConstraint("state IN ('NEW','INITIALIZING','READY','BLOCKED','TERMINAL')",
                           name="ck_egress_invocation_state"),
        {"schema": "runtime"},
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid())
    operation_key: Mapped[str] = mapped_column(sa.Text())
    skill_key: Mapped[str] = mapped_column(sa.Text())
