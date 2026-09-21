from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Artifact：run/task 二选一（XOR）；conversation 可为空（定时任务产物）；媒体类型对齐设计宽度
    op.alter_column("artifact", "run_id", nullable=True, schema="runtime")
    op.alter_column("artifact", "conversation_id", nullable=True, schema="runtime")
    op.alter_column(
        "artifact", "artifact_type", type_=sa.String(64), existing_nullable=False, schema="runtime"
    )
    op.alter_column(
        "artifact", "media_type", type_=sa.String(128), existing_nullable=False, schema="runtime"
    )
    op.create_check_constraint(
        "ck_artifact_run_task_xor",
        "artifact",
        "(run_id IS NOT NULL) <> (task_id IS NOT NULL)",
        schema="runtime",
    )
    # Memory：写入策略默认 CONTROLLED；category 由调用方显式给出
    op.alter_column(
        "user_memory",
        "write_policy",
        server_default=sa.text("'CONTROLLED'"),
        existing_nullable=False,
        schema="runtime",
    )
    op.alter_column(
        "user_memory",
        "category",
        server_default=None,
        existing_nullable=False,
        schema="runtime",
    )


def downgrade() -> None:
    op.alter_column(
        "user_memory",
        "category",
        server_default=sa.text("'general'"),
        existing_nullable=False,
        schema="runtime",
    )
    op.alter_column(
        "user_memory",
        "write_policy",
        server_default=sa.text("'APPEND'"),
        existing_nullable=False,
        schema="runtime",
    )
    op.drop_constraint("ck_artifact_run_task_xor", "artifact", schema="runtime", type_="check")
    op.alter_column(
        "artifact", "media_type", type_=sa.String(64), existing_nullable=False, schema="runtime"
    )
    op.alter_column(
        "artifact", "artifact_type", type_=sa.String(32), existing_nullable=False, schema="runtime"
    )
    op.alter_column("artifact", "conversation_id", nullable=False, schema="runtime")
    op.alter_column("artifact", "run_id", nullable=False, schema="runtime")
