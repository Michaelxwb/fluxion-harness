"""移除从未使用的 langgraph schema。

0001 建出的 `langgraph` schema 没有任何代码写入；执行核心去框架化后它只剩死表面。
upgrade 删除该 schema，downgrade 重建空 schema 以支持回滚。
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS langgraph")


def downgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
