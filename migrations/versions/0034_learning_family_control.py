"""族级学习/注入暂停开关。"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_family_control",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("create_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("update_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        schema="runtime",
    )
    op.create_index(
        "uq_learning_family_control",
        "learning_family_control",
        ["tenant_id", "family"],
        unique=True,
        schema="runtime",
        postgresql_where=sa.text("is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_learning_family_control", table_name="learning_family_control", schema="runtime")
    op.drop_table("learning_family_control", schema="runtime")
