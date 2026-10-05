from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 平台设置版本表（设计 §3.3 / ADR-02）：append-only —— 每次保存插入一行新版本，
    # 当前版本 = 该租户最大 revision。**无回填**：未保存过的租户本就没有设置行，
    # 读取侧回落 schema 默认（TASK-003），补一行"默认值"是把"没配过"伪造成"配过"。
    #
    # 乐观并发不靠行锁：落败的 INSERT 撞 `uq_platform_setting_tenant_revision`
    # （partial unique `WHERE is_deleted = false`）即返回版本冲突。
    #
    # `actor_user_id` 是 `console_account.id` 的**逻辑引用，不建物理 FK**：版本行不可变、
    # 永不清理，物理 FK 会把保存过设置的管理员账号永久钉住；同 Schema 既有先例为
    # `control.config_audit_log.actor_user_id`（同为逻辑引用）。
    op.create_table(
        "platform_setting",
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
        sa.Column("revision", sa.BigInteger(), nullable=False),
        # 整份设置文档（设计 §2.3.2 的 41 个叶子）；唯一的查询键是 tenant_id + revision，
        # 二者都是独立列，设置内容本身不参与查询 ⇒ jsonb 是正确选择（RULE-data-001）。
        sa.Column("settings_json", JSONB(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Index(
            "uq_platform_setting_tenant_revision",
            "tenant_id",
            "revision",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        # 读当前版本 / 版本历史分页
        sa.Index("ix_platform_setting_tenant_revision_desc", "tenant_id", sa.text("revision DESC")),
        schema="control",
    )


def downgrade() -> None:
    op.drop_table("platform_setting", schema="control")
