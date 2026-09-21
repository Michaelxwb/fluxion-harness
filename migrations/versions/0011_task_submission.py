from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # §3.3.8：创建类 POST 的提交幂等记录（RULE-api-002）。
    # 新增独立表，不改写已发布的 0002。
    op.create_table(
        "task_submission",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "create_time", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "update_time", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("endpoint", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("response_json", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_submission"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.task_execution.id"], name="task_submission_task_id_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["task.task_schedule.id"], name="task_submission_schedule_id_fkey"
        ),
        schema="task",
    )
    op.create_index(
        "uq_task_submission_tenant_key_endpoint",
        "task_submission",
        ["tenant_id", "idempotency_key", "endpoint"],
        unique=True,
        schema="task",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_task_submission_task_create_time",
        "task_submission",
        ["task_id", "create_time"],
        schema="task",
    )
    op.create_index(
        "ix_task_submission_schedule_create_time",
        "task_submission",
        ["schedule_id", "create_time"],
        schema="task",
    )


def downgrade() -> None:
    op.drop_table("task_submission", schema="task")
