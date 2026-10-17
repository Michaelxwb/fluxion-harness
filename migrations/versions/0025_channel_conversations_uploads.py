"""Conversation entry attribution, list index, and staging uploads/pointers.

0025 扩展现有 `runtime.conversation` 并新建 `runtime.channel_upload` /
`runtime.channel_conversation`（design §3.3）：

- `conversation` 新增 `channel_account_id`（逻辑 UUID，跨 Owner **不建**物理 FK）、
  `external_thread_id`、`last_message_at`，以及活跃列表索引
  `(tenant_id,user_id,channel_account_id,agent_id,last_message_at DESC,id DESC) WHERE is_deleted = false`；
- 旧会话入口归属只按可靠 Run 记录回填：`channel_json->>'bot_id'` 命中**同租户、未删除**的
  `control.bot_account`，且整个会话只解析出唯一入口（多个入口并存 ⇒ 无法证实，保持 NULL）；
  未知 bot_id、已停用入口、无渠道记录、软删会话一律不回填——保留审计，**不冒认成网页历史**。
  `external_thread_id` 取最近一次可靠运行的 `external_conversation_id`（1:1 会话回退
  `external_user_id`）；`last_message_at` 由真实可见消息事务维护，迁移不臆造；回填不是业务
  变更，不触碰 `update_time`；
- `channel_upload`：运行前的暂存登记（**不是** artifact 行）。活跃 `(tenant_id,storage_key)`
  唯一是不可变键（同消息重投按 upload_id 重复验证）；`expires_at > create_time`、`size > 0`、
  `kind` 与契约枚举（IMAGE/DOCUMENT/OTHER）一致都是数据库事实；
  `(tenant_id,user_id,entry_id,expires_at)` 支撑清理（含过期/软删行，故不设谓词）；
- `channel_conversation`：无显式会话入口的当前指针，active unique
  `(tenant_id,user_id,agent_id,channel_account_id,external_thread_id)`，`conversation_id`
  是同 Owner 物理 FK；Webchat 写目标不依赖该指针。

纯 DDL + 一次数据回填，不读环境变量；回退仅在两张新表为空且会话没有入口归属/消息时间时
允许，否则 `CHANNEL_ENTRY_DOWNGRADE_REQUIRES_BACKUP`——结构回退会静默丢掉入口归属、
暂存登记与指针状态。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.schema import SchemaItem

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

NEW_COLUMNS = ("channel_account_id", "external_thread_id", "last_message_at")
TABLES = ("channel_upload", "channel_conversation")

BACKFILL_ENTRY = """
WITH resolved AS (
    SELECT r.conversation_id AS conversation_id,
           b.id AS bot_account_id,
           COALESCE(NULLIF(r.channel_json ->> 'external_conversation_id', ''),
                    NULLIF(r.channel_json ->> 'external_user_id', '')) AS thread_id,
           r.create_time AS create_time,
           r.id AS run_id
    FROM runtime.run_record AS r
    JOIN control.bot_account AS b
      ON b.bot_id = r.channel_json ->> 'bot_id'
     AND b.tenant_id = r.tenant_id
     AND b.is_deleted = false
    WHERE r.is_deleted = false
      AND r.channel_json ->> 'bot_id' IS NOT NULL
),
single_entry AS (
    SELECT conversation_id, (array_agg(DISTINCT bot_account_id))[1] AS bot_account_id
    FROM resolved
    GROUP BY conversation_id
    HAVING count(DISTINCT bot_account_id) = 1
),
latest AS (
    SELECT DISTINCT ON (resolved.conversation_id)
           resolved.conversation_id AS conversation_id,
           resolved.thread_id AS thread_id
    FROM resolved
    JOIN single_entry ON single_entry.conversation_id = resolved.conversation_id
    ORDER BY resolved.conversation_id, resolved.create_time DESC, resolved.run_id DESC
)
UPDATE runtime.conversation AS c
SET channel_account_id = single_entry.bot_account_id,
    external_thread_id = latest.thread_id
FROM single_entry
JOIN latest ON latest.conversation_id = single_entry.conversation_id
WHERE c.id = single_entry.conversation_id
  AND c.is_deleted = false
  AND c.channel_account_id IS NULL
"""


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _extend_conversation() -> None:
    op.add_column("conversation", sa.Column("channel_account_id", sa.Uuid()), schema="runtime")
    op.add_column("conversation", sa.Column("external_thread_id", sa.String(256)), schema="runtime")
    op.add_column("conversation", sa.Column("last_message_at", sa.DateTime(timezone=True)), schema="runtime")
    op.create_index(
        "ix_conversation_tenant_user_channel_agent_last_message",
        "conversation",
        [
            "tenant_id",
            "user_id",
            "channel_account_id",
            "agent_id",
            sa.text("last_message_at DESC"),
            sa.text("id DESC"),
        ],
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_upload() -> None:
    op.create_table(
        "channel_upload",
        *_standard(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("checksum", sa.String(128), nullable=False),
        sa.Column("filename", sa.String(255)),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size > 0", name="ck_channel_upload_size"),
        sa.CheckConstraint("kind IN ('IMAGE','DOCUMENT','OTHER')", name="ck_channel_upload_kind"),
        sa.CheckConstraint("expires_at > create_time", name="ck_channel_upload_expires_after_created"),
        schema="runtime",
    )
    op.create_index(
        "uq_channel_upload_tenant_storage_key",
        "channel_upload",
        ["tenant_id", "storage_key"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_channel_upload_tenant_user_entry_expires",
        "channel_upload",
        ["tenant_id", "user_id", "entry_id", "expires_at"],
        schema="runtime",
    )


def _create_pointer() -> None:
    op.create_table(
        "channel_conversation",
        *_standard(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("channel_account_id", sa.Uuid(), nullable=False),
        sa.Column("external_thread_id", sa.String(256), nullable=False, server_default=sa.text("''")),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["runtime.conversation.id"],
            name="fk_channel_conversation_conversation_id_conversation",
        ),
        schema="runtime",
    )
    op.create_index(
        "uq_channel_conversation_tenant_user_entry_thread",
        "channel_conversation",
        ["tenant_id", "user_id", "agent_id", "channel_account_id", "external_thread_id"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )


def upgrade() -> None:
    _extend_conversation()
    _create_upload()
    _create_pointer()
    op.execute(sa.text(BACKFILL_ENTRY))


def downgrade() -> None:
    bind = op.get_bind()
    staged = bind.execute(sa.text(
        "SELECT (SELECT count(*) FROM runtime.channel_upload)"
        " + (SELECT count(*) FROM runtime.channel_conversation)"
    )).scalar_one()
    attributed = bind.execute(sa.text(
        "SELECT count(*) FROM runtime.conversation WHERE is_deleted = false"
        " AND (channel_account_id IS NOT NULL OR external_thread_id IS NOT NULL"
        " OR last_message_at IS NOT NULL)"
    )).scalar_one()
    if staged or attributed:
        raise RuntimeError(
            "CHANNEL_ENTRY_DOWNGRADE_REQUIRES_BACKUP: 0025 的暂存上传/入口指针与入口归属将被结构回退"
            f"删除（登记行 {staged} 行、已归属会话 {attributed} 个）；先在维护窗口留档，"
            "确认暂存清理与历史归属保留策略后再回退"
        )
    op.drop_index(
        "uq_channel_conversation_tenant_user_entry_thread",
        table_name="channel_conversation",
        schema="runtime",
    )
    op.drop_table("channel_conversation", schema="runtime")
    op.drop_index("uq_channel_upload_tenant_storage_key", table_name="channel_upload", schema="runtime")
    op.drop_index("ix_channel_upload_tenant_user_entry_expires", table_name="channel_upload", schema="runtime")
    op.drop_table("channel_upload", schema="runtime")
    op.drop_index(
        "ix_conversation_tenant_user_channel_agent_last_message",
        table_name="conversation",
        schema="runtime",
    )
    for name in NEW_COLUMNS:
        op.drop_column("conversation", name, schema="runtime")
