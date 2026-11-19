"""External platform identity and entry grants, plus the per-user auth revision.

External identities are user-level: one row answers「哪个外部标识对应哪个平台用户」.
Entry grants are separate: one row answers「这个身份能在哪个入口下使用」. Splitting
them keeps history intact when an entry is unbound and rebound.

`platform_user.auth_revision` starts at 1 for existing rows; any event that changes
what the user may reach bumps it so cached auth decisions can be invalidated.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# 编号说明：main 分支的 `0051_audit_operation_view_index.py` 占用 "0051"。
# 本文件起初也声明 "0051" 且 down_revision 都是 "0050"，合并后 alembic 报
# 「Revision 0051 is present more than once」，head 分叉成 0051 / 0052，
# 启动自检因库在 "0051"、代码 head 为 "0052" 而拒绝启动。
# 现按连号重排，保持单链：
#   0049 -> 0050 -> 0051(审计视图索引) -> 0052(本文件) -> 0053(认证缓存同步状态)
revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_user",
        sa.Column("auth_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        schema="control",
    )

    op.create_table(
        "external_identity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("platform_key", sa.String(64), nullable=False),
        sa.Column("platform_user_id", sa.Uuid(), nullable=False),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        sa.Column("identity_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["platform_user_id"],
            ["control.platform_user.id"],
            name="fk_external_identity_platform_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_identity"),
        schema="control",
    )
    op.create_index(
        "uq_external_identity_tenant_platform_hash",
        "external_identity",
        ["tenant_id", "platform_key", "identity_hash"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_external_identity_tenant_user",
        "external_identity",
        ["tenant_id", "platform_user_id"],
        schema="control",
    )

    op.create_table(
        "entry_access_grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("external_identity_id", sa.Uuid(), nullable=False),
        sa.Column("bot_account_id", sa.Uuid(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["external_identity_id"],
            ["control.external_identity.id"],
            name="fk_entry_access_grant_identity",
        ),
        sa.ForeignKeyConstraint(
            ["bot_account_id"],
            ["control.bot_account.id"],
            name="fk_entry_access_grant_bot_account",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_entry_access_grant"),
        schema="control",
    )
    op.create_index(
        "uq_entry_access_grant_tenant_identity_bot",
        "entry_access_grant",
        ["tenant_id", "external_identity_id", "bot_account_id"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_entry_access_grant_tenant_bot_identity",
        "entry_access_grant",
        ["tenant_id", "bot_account_id", "external_identity_id"],
        schema="control",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entry_access_grant_tenant_bot_identity", table_name="entry_access_grant", schema="control"
    )
    op.drop_index(
        "uq_entry_access_grant_tenant_identity_bot", table_name="entry_access_grant", schema="control"
    )
    op.drop_table("entry_access_grant", schema="control")

    op.drop_index("ix_external_identity_tenant_user", table_name="external_identity", schema="control")
    op.drop_index(
        "uq_external_identity_tenant_platform_hash", table_name="external_identity", schema="control"
    )
    op.drop_table("external_identity", schema="control")

    op.drop_column("platform_user", "auth_revision", schema="control")
