from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 入站事件审计（设计 §3.3 / API-10）：接收 / 拒绝 / 失败各一条。
    # 刻意没有自由 JSON 列 —— 取件凭据（aes_key、媒体 URL）在结构上无处可放。
    op.create_table(
        "im_inbound_audit",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "create_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "update_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("bot_id", sa.String(128), nullable=False),
        sa.Column("external_message_id", sa.String(128), nullable=False),
        sa.Column("external_user_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Index(
            "ix_im_inbound_audit_tenant_create_time",
            "tenant_id",
            sa.text("create_time DESC"),
        ),
        sa.Index("ix_im_inbound_audit_external_message_id", "external_message_id"),
        # 幂等：企微会重投（E-07 同源），同一消息的同一结局只留一行（软删唯一口径）
        sa.Index(
            "uq_im_inbound_audit_message_outcome",
            "tenant_id",
            "channel",
            "external_message_id",
            "outcome",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        schema="control",
    )


def downgrade() -> None:
    op.drop_table("im_inbound_audit", schema="control")
