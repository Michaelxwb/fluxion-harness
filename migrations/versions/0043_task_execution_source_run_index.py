"""按来源 Run 批量读取 Task 的租户前缀索引。

单条执行链路导出需要 `WHERE tenant_id = ? AND source_run_id = ?` 取一次 Run 派生出的
全部 Task（再沿 parent_id 递归子任务）。没有该索引时任务表按历史全表扫描，导出会随
Task 总量线性变慢。partial index 只覆盖未软删行，写入侧只多一个很小的索引项。
"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_task_execution_source_run"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "task_execution",
        ["tenant_id", "source_run_id"],
        schema="task",
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="task_execution", schema="task")
