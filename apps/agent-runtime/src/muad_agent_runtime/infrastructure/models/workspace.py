"""``task.workspace`` / ``task.workspace_lease``（Runtime 侧执行租约视图）。

表的所有权仍在 Worker 的 task schema（迁移 0037）；Runtime 只做执行前 acquire/执行后
release，不承担 TTL 清理。模型字段必须与 `migrations/versions/0037_skill_sandbox_schema.py`
和 Worker 侧模型一致，避免两个部署单元对同一张表产生不同理解。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin

WORKSPACE_STATES = ("ACTIVE", "DELETING", "DELETED")


class Workspace(StandardColumnsMixin, Base):
    __tablename__ = "workspace"
    __table_args__ = (
        sa.Index(
            "uq_workspace_tenant_user_agent",
            "tenant_id",
            "user_id",
            "agent_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_workspace_last_access", "last_access_time"),
        sa.Index("ix_workspace_state", "state"),
        sa.CheckConstraint(
            "state IN ('ACTIVE','DELETING','DELETED')",
            name="ck_workspace_state",
        ),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    last_access_time: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    size_bytes: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, server_default=sa.text("0")
    )
    file_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")
    )
    cleanup_error: Mapped[str | None] = mapped_column(sa.Text())


class WorkspaceLease(StandardColumnsMixin, Base):
    __tablename__ = "workspace_lease"
    __table_args__ = (
        sa.Index(
            "uq_workspace_lease_workspace_execution",
            "workspace_id",
            "execution_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_workspace_lease_workspace", "workspace_id"),
        sa.Index("ix_workspace_lease_until", "lease_until"),
        {"schema": "task"},
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.workspace.id"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    execution_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


__all__ = ["WORKSPACE_STATES", "Workspace", "WorkspaceLease"]
