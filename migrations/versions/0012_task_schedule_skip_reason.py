from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 设计 v1.3 §3.3.1：E-02/E-04 的失败/跳过原因落点。
    # 只保留「最近一次」，不建审计表；不改写已发布的 0002。
    op.add_column(
        "task_schedule",
        sa.Column("last_error_code", sa.String(64), nullable=True),
        schema="task",
    )
    op.add_column(
        "task_schedule",
        sa.Column("last_error_message", sa.Text(), nullable=True),
        schema="task",
    )
    op.add_column(
        "task_schedule",
        sa.Column("last_skipped_at", sa.DateTime(timezone=True), nullable=True),
        schema="task",
    )


def downgrade() -> None:
    op.drop_column("task_schedule", "last_skipped_at", schema="task")
    op.drop_column("task_schedule", "last_error_message", schema="task")
    op.drop_column("task_schedule", "last_error_code", schema="task")
