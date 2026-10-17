"""Console chat access tokens (digest-only) and independent CHAT sessions.

0024 建 `control.chat_access_token` / `control.chat_session`（design §3.3）：

- **只存摘要**：`token_hash` varchar(64) + 小写十六进制形态 CHECK；时间列全部带时区，
  `expires_at > issued_at` 是数据库事实；
- `platform_user_id` / `access_token_id` 为同 Owner（control → control）物理 FK；
- 每用户至多一个未撤销 Token：partial unique `(tenant_id, platform_user_id)`
  `WHERE is_deleted = false AND revoked_at IS NULL`；`(tenant_id, platform_user_id, revision)` 与
  `token_hash` 的 partial unique 都以软删为界 ⇒ 旧记录保留（含已过期未撤销者，必须先显式撤销再
  重签），tombstone 不阻塞重录；
- 会话按来源 Token 可并存多条，`ix_chat_session_access_token`（活跃）支撑关联撤销。

纯 DDL，不读环境变量；回退仅在两张表为空时允许，否则 `CHAT_AUTH_DOWNGRADE_REQUIRES_BACKUP`
——已签发的用户凭据与登录会话不能被结构回退静默丢掉。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.schema import SchemaItem

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TABLES = ("chat_access_token", "chat_session")
DIGEST_SHAPE = "token_hash ~ '^[0-9a-f]{64}$'"


def _standard() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
    ]


def _create_token() -> None:
    op.create_table(
        "chat_access_token",
        *_standard(),
        sa.Column("platform_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["platform_user_id"],
            ["control.platform_user.id"],
            name="fk_chat_access_token_platform_user_id_platform_user",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_chat_access_token_revision"),
        sa.CheckConstraint("expires_at > issued_at", name="ck_chat_access_token_expires_after_issued"),
        sa.CheckConstraint(DIGEST_SHAPE, name="ck_chat_access_token_token_hash"),
        schema="control",
    )
    op.create_index(
        "uq_chat_access_token_token_hash",
        "chat_access_token",
        ["token_hash"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_chat_access_token_tenant_user_revision",
        "chat_access_token",
        ["tenant_id", "platform_user_id", "revision"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_chat_access_token_tenant_user_unrevoked",
        "chat_access_token",
        ["tenant_id", "platform_user_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false AND revoked_at IS NULL"),
    )


def _create_session() -> None:
    op.create_table(
        "chat_session",
        *_standard(),
        sa.Column("access_token_id", sa.Uuid(), nullable=False),
        sa.Column("platform_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["access_token_id"],
            ["control.chat_access_token.id"],
            name="fk_chat_session_access_token_id_chat_access_token",
        ),
        sa.ForeignKeyConstraint(
            ["platform_user_id"],
            ["control.platform_user.id"],
            name="fk_chat_session_platform_user_id_platform_user",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_chat_session_expires_after_issued"),
        sa.CheckConstraint(DIGEST_SHAPE, name="ck_chat_session_token_hash"),
        schema="control",
    )
    op.create_index(
        "uq_chat_session_token_hash",
        "chat_session",
        ["token_hash"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_chat_session_tenant_user_expires",
        "chat_session",
        ["tenant_id", "platform_user_id", "expires_at"],
        schema="control",
    )
    op.create_index(
        "ix_chat_session_access_token",
        "chat_session",
        ["access_token_id"],
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def upgrade() -> None:
    _create_token()
    _create_session()


def downgrade() -> None:
    bind = op.get_bind()
    populated = sorted(
        table
        for table in TABLES
        if bind.execute(sa.text(f"SELECT count(*) FROM control.{table}")).scalar_one() > 0
    )
    if populated:
        raise RuntimeError(
            "CHAT_AUTH_DOWNGRADE_REQUIRES_BACKUP: control 聊天凭据表已有数据"
            f"（{', '.join(populated)}），回退会丢掉已签发的用户 Token 与登录会话；"
            "先在维护窗口留档，确认重签/失效处理方案后再回退"
        )
    for table in reversed(TABLES):
        op.drop_table(table, schema="control")
