"""expand: 密钥明文列（SecretRef 移除专项，阶段 1/2）

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NEW_COLUMNS = (
    ("model_definition", "api_key", sa.Text()),
    ("bot_account", "secret", sa.Text()),
    ("mcp_server", "auth_secret", sa.Text()),
    ("project_platform", "auth_secret", sa.Text()),
)


def upgrade() -> None:
    for table, column, column_type in NEW_COLUMNS:
        op.add_column(table, sa.Column(column, column_type), schema="control")
    for table in ("user_credential_ref", "shared_credential_ref"):
        op.add_column(
            table,
            sa.Column("credential_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            schema="control",
        )


def downgrade() -> None:
    for table in ("user_credential_ref", "shared_credential_ref"):
        op.drop_column(table, "credential_json", schema="control")
    for table, column, _ in reversed(NEW_COLUMNS):
        op.drop_column(table, column, schema="control")
