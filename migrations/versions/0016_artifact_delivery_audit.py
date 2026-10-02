from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 交付审计（设计 §3.3 / API-03）："谁在何时把哪个产物交付给哪个路由、结果如何"。
    # 与上期 im_inbound_audit **刻意不同形**：路由用 (channel, route_key)，不拆成渠道私有的
    # bot_id/external_user_id —— 接 web chat 时那两个列会填不出真值。
    # 同样没有自由 JSON 列：交付凭据与取件令牌在结构上无处可放。
    # **无回填**：历史产物没有交付记录，"补一批 NULL 行"是把不存在的事实伪造成数据。
    op.create_table(
        "artifact_delivery_audit",
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
        # 跨 Owner Schema（control vs runtime）⇒ 逻辑 UUID 引用，**不建物理 FK**
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("route_key", sa.String(256), nullable=False),
        sa.Column("delivery_key", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Index(
            "ix_artifact_delivery_audit_tenant_time",
            "tenant_id",
            sa.text("create_time DESC"),
        ),
        sa.Index("ix_artifact_delivery_audit_artifact", "artifact_id"),
        sa.Index("ix_artifact_delivery_audit_route", "tenant_id", "channel", "route_key"),
        sa.Index("ix_artifact_delivery_audit_delivery_key", "delivery_key"),
        # **幂等键的唯一权威定义**：同一产物对同一路由只有一行。
        # 刻意不含 delivery_key（传输路径不同、事实相同）与 outcome（它是该行的当前状态）。
        sa.Index(
            "uq_artifact_delivery_audit_target",
            "tenant_id",
            "artifact_id",
            "route_key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        schema="control",
    )


def downgrade() -> None:
    op.drop_table("artifact_delivery_audit", schema="control")
