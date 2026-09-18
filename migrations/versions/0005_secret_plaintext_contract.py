"""contract: 删除 SecretRef 旧列（SecretRef 移除专项，阶段 2/2）

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

OLD_COLUMNS = (
    ("model_definition", "secret_ref", sa.String(256)),
    ("bot_account", "secret_ref", sa.String(256)),
    ("mcp_server", "auth_secret_ref", sa.String(256)),
    ("user_credential_ref", "secret_ref", sa.String(256)),
    ("shared_credential_ref", "secret_ref", sa.String(256)),
)


def upgrade() -> None:
    for table, column, _ in OLD_COLUMNS:
        op.drop_column(table, column, schema="control")


def downgrade() -> None:
    for table, column, column_type in OLD_COLUMNS:
        op.add_column(table, sa.Column(column, column_type), schema="control")
