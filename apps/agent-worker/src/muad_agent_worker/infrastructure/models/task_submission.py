import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin

UNIQUE_SUBMISSION_INDEX = "uq_task_submission_tenant_key_endpoint"


class TaskSubmission(StandardColumnsMixin, Base):
    """创建类 POST（/internal/tasks、/internal/schedules）的提交幂等记录。

    设计见 09-task-schedule.backend.design.md §3.3.8；与模块 08 的
    runtime.run_submission 同构：partial unique 兜底并发，保存首次提交的
    请求指纹与首次响应快照供同 key 重放。
    """

    __tablename__ = "task_submission"
    __table_args__ = (
        sa.Index(
            UNIQUE_SUBMISSION_INDEX,
            "tenant_id",
            "idempotency_key",
            "endpoint",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_task_submission_task_create_time", "task_id", "create_time"),
        sa.Index("ix_task_submission_schedule_create_time", "schedule_id", "create_time"),
        {"schema": "task"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_execution.id")
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), sa.ForeignKey("task.task_schedule.id")
    )
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
