from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _standard_columns() -> list[sa.Column]:
    return [
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
    ]


def _create_control_console_account() -> None:
    op.create_table(
        "console_account",
        *_standard_columns(),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default=sa.text("'BUILDER'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        schema="control",
    )
    op.create_index(
        "uq_console_account_tenant_username",
        "console_account",
        ["tenant_id", "username"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )


def _create_control_console_session() -> None:
    op.create_table(
        "console_session",
        *_standard_columns(),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("control.console_account.id"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("source_ip", sa.String(64)),
        schema="control",
    )
    op.create_index(
        "uq_console_session_token_hash",
        "console_session",
        ["token_hash"],
        unique=True,
        schema="control",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_console_session_account_expires",
        "console_session",
        ["account_id", "expires_at"],
        schema="control",
    )


def upgrade() -> None:
    _create_control_console_account()
    _create_control_console_session()


def downgrade() -> None:
    op.drop_table("console_session", schema="control")
    op.drop_table("console_account", schema="control")
