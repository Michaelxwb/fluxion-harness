"""运行审计操作视图：租户 + 时间倒序分页索引（行为等价，只加索引）。

操作视图的主查询在 `runtime.run_record` 上按租户过滤、按 `create_time DESC` 排序分页；
现有索引只覆盖 conversation / agent+status / task_family / trace_id，没有该组合。
部分索引谓词与查询一致（未软删行），不改表结构、不回填。
"""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

INDEX = "ix_run_record_tenant_create_time"


def upgrade() -> None:
    op.create_index(
        INDEX,
        "run_record",
        ["tenant_id", sa.text("create_time DESC")],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="run_record", schema="runtime")
