from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 模块 09 review 修复：Run 持久化入站渠道上下文（bot_id / external_user_id /
    # external_conversation_id），后台 Task/Schedule 据此建立 DeliveryRoute；
    # 跨 Pod 恢复的 Run 同样可读。只存接收方标识，不含 Bot Secret。
    op.add_column(
        "run_record",
        sa.Column("channel_json", postgresql.JSONB(), nullable=True),
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_column("run_record", "channel_json", schema="runtime")
