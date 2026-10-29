"""Run admission 租户护栏：在飞 Run 的部分索引（P2，行为等价）。

`count_open_runs` 只判定「租户在飞 Run 是否达到上限」，不需要精确总数；配合
`LIMIT max` 让大表上的计数提前停止。索引只覆盖 `admission_released_at IS NULL`
的行（即未释放的在飞 Run），与查询谓词完全一致。
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

INDEX = "ix_run_record_open_tenant"


def upgrade() -> None:
    op.create_index(
        INDEX,
        "run_record",
        ["tenant_id"],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text("admission_released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="run_record", schema="runtime")
