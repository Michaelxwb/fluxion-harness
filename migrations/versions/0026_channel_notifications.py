"""Per-user notification feed, outbound delivery leases, and stable receipts.

0026 建 `runtime.channel_notification_feed` / `runtime.channel_notification`（design §3.3）：

- feed 是每用户的**稳定注册行**：完整唯一 `(tenant_id,user_id)`（不是 partial），注册行被
  `is_deleted = false` CHECK 拒绝软删且 `last_seq >= 0`——清理通知/重新登录都不能把水位
  重置回零；
- 通知的 `(tenant_id,user_id,seq)` 与 `(tenant_id,delivery_key)` 也是**完整唯一**（无谓词）：
  隐藏/清理只留 tombstone，序号与交付回执不放回——“仍可能重投/UNKNOWN 的结果不得清除唯一
  回执”，超过重放保留期的旧 delivery_key 不能当成新投递重新发送；
- `read_at` 与 `delivery_state` 分离：送达不代替已读，已读不要求送达；
- 出站投递把 `PENDING` 意图升级为 `DELIVERING` 租约（lease_owner/lease_epoch/lease_until），
  ack 后 `DELIVERED` 并保留**首次结果 JSON**（`delivery_result`）供重放恢复；`UNKNOWN`
  保留核实，`NOT_REQUIRED` 是网页持久入箱；`delivery_state` 无 server default，写入方必须
  显式选择，不静默把入箱通知当成出站意图；
- 投递 partial 索引（due/lease）不带 `is_deleted` 谓词：隐藏通知不丢投递回执与重试资格；
- 同 Owner 的 feed/conversation/run 是物理 FK；user/entry/task 是逻辑 UUID（无 FK）；
  DTO 的 agent_id/summary 在读取时由 conversation 与引用投影，不落列。

纯 DDL，无数据回填（不存在历史通知行；feed 由首次写入在锁内登记）。回退仅在两张新表都为空
时允许，否则 `CHANNEL_NOTIFICATION_DOWNGRADE_REQUIRES_BACKUP`——结构回退会静默丢掉 feed
水位、未读状态、投递租约与交付回执。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.schema import SchemaItem

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

TABLES = ("channel_notification_feed", "channel_notification")


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _create_feed() -> None:
    op.create_table(
        "channel_notification_feed",
        *_standard(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_channel_notification_feed_tenant_user"),
        sa.CheckConstraint("last_seq >= 0", name="ck_channel_notification_feed_last_seq"),
        sa.CheckConstraint("is_deleted = false", name="ck_channel_notification_feed_not_deleted"),
        schema="runtime",
    )


def _create_notification() -> None:
    op.create_table(
        "channel_notification",
        *_standard(),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid()),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("delivery_key", sa.String(128), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("event_id", sa.Uuid()),
        sa.Column(
            "artifact_ids",
            postgresql.ARRAY(sa.Uuid()),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column("delivery_state", sa.String(16), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("delivery_result", postgresql.JSONB()),
        sa.ForeignKeyConstraint(
            ["feed_id"],
            ["runtime.channel_notification_feed.id"],
            name="fk_channel_notification_feed_id_channel_notification_feed",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["runtime.conversation.id"],
            name="fk_channel_notification_conversation_id_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runtime.run_record.id"],
            name="fk_channel_notification_run_id_run_record",
        ),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "seq", name="uq_channel_notification_tenant_user_seq"
        ),
        sa.UniqueConstraint(
            "tenant_id", "delivery_key", name="uq_channel_notification_tenant_delivery_key"
        ),
        sa.CheckConstraint(
            "kind IN ('RUN_COMPLETED','RUN_WAITING_INPUT','TASK_RESULT')",
            name="ck_channel_notification_kind",
        ),
        sa.CheckConstraint("seq > 0", name="ck_channel_notification_seq"),
        sa.CheckConstraint(
            "delivery_state IN ('PENDING','DELIVERING','DELIVERED','UNKNOWN','NOT_REQUIRED')",
            name="ck_channel_notification_delivery_state",
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND lease_epoch >= 0", name="ck_channel_notification_counters"
        ),
        schema="runtime",
    )
    op.create_index(
        "ix_channel_notification_tenant_user_unread",
        "channel_notification",
        ["tenant_id", "user_id", "seq"],
        schema="runtime",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_channel_notification_due",
        "channel_notification",
        ["tenant_id", "next_attempt_at"],
        schema="runtime",
        postgresql_where=sa.text("delivery_state = 'PENDING'"),
    )
    op.create_index(
        "ix_channel_notification_lease",
        "channel_notification",
        ["tenant_id", "lease_until"],
        schema="runtime",
        postgresql_where=sa.text("delivery_state = 'DELIVERING'"),
    )


def upgrade() -> None:
    _create_feed()
    _create_notification()


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT (SELECT count(*) FROM runtime.channel_notification_feed)"
        " + (SELECT count(*) FROM runtime.channel_notification)"
    )).scalar_one()
    if rows:
        raise RuntimeError(
            "CHANNEL_NOTIFICATION_DOWNGRADE_REQUIRES_BACKUP: 0026 的 feed 水位、未读状态、"
            f"投递租约与交付回执将被结构回退删除（现存 {rows} 行）；先在维护窗口留档，"
            "确认通知/回执保留策略后再回退"
        )
    for name in (
        "ix_channel_notification_lease",
        "ix_channel_notification_due",
        "ix_channel_notification_tenant_user_unread",
    ):
        op.drop_index(name, table_name="channel_notification", schema="runtime")
    op.drop_table("channel_notification", schema="runtime")
    op.drop_table("channel_notification_feed", schema="runtime")
