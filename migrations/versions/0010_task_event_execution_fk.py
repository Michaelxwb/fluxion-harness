from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

FK_NAME = "task_event_task_id_fkey"


def upgrade() -> None:
    # RULE-data-001：同一 Owner Schema 的表必须用物理 FK 关联。
    # 0002 建立的 task.task_event 漏了 task_id -> task.task_execution.id 外键，
    # 这里用 expand migration 补齐，不重写已发布的 0002。
    op.create_foreign_key(
        FK_NAME,
        "task_event",
        "task_execution",
        ["task_id"],
        ["id"],
        source_schema="task",
        referent_schema="task",
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "task_event", schema="task", type_="foreignkey")
