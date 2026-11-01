"""经验管理读模型：族 Run 增量汇总表与查询索引。"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "family_run_stat",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("run_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        schema="runtime",
    )
    op.create_index(
        "uq_family_run_stat_day",
        "family_run_stat",
        ["tenant_id", "family", "day"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_family_run_stat_window",
        "family_run_stat",
        ["tenant_id", sa.text("day DESC"), "family"],
        unique=False,
        schema="runtime",
    )
    op.create_index(
        "ix_user_memory_state_time",
        "user_memory",
        ["tenant_id", "activation_state", sa.text("update_time DESC")],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text(
            "is_deleted = false AND category IN ('PROCEDURE','FAILURE_PATTERN','VERIFICATION')"
        ),
    )
    op.create_index(
        "ix_user_memory_family_state",
        "user_memory",
        ["tenant_id", "task_signature", "activation_state", sa.text("update_time DESC")],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text(
            "is_deleted = false AND category IN ('PROCEDURE','FAILURE_PATTERN','VERIFICATION')"
        ),
    )
    op.create_index(
        "ix_user_memory_user_time",
        "user_memory",
        ["tenant_id", "user_id", sa.text("update_time DESC"), sa.text("id DESC")],
        unique=False,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_user_memory_user_time", table_name="user_memory", schema="runtime")
    op.drop_index("ix_user_memory_family_state", table_name="user_memory", schema="runtime")
    op.drop_index("ix_user_memory_state_time", table_name="user_memory", schema="runtime")
    op.drop_index("ix_family_run_stat_window", table_name="family_run_stat", schema="runtime")
    op.drop_index("uq_family_run_stat_day", table_name="family_run_stat", schema="runtime")
    op.drop_table("family_run_stat", schema="runtime")
