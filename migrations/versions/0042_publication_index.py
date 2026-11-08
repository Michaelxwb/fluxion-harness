"""站内交付取件的发布索引：只索引 `ARTIFACT_CREATED` 行，按 (会话, 产物) 点查。

不建这条索引时，下载的发布引用查询会沿 `(conversation_id, create_time)` 扫过整个会话的
增量事件（实测 2 万行会话：20000 行过滤、921 buffers、约 2.5 ms），随会话历史线性增长。
partial 索引只覆盖发布行（每个交付一条），命中的是点查；写入侧只多一条很小的索引项。

生产若表已很大，应在业务低峰用 `CREATE INDEX CONCURRENTLY` 单独建（迁移内保证schema一致，
不负责在线建索引的停机窗口）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_canonical_event_publication"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "canonical_event",
        ["conversation_id", "artifact_id"],
        schema="runtime",
        postgresql_where=sa.text("event_type = 'ARTIFACT_CREATED' AND is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="canonical_event", schema="runtime")
