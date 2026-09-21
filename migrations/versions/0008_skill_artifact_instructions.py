"""expand: skill_artifact 增加 SKILL.md 正文快照列

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_artifact",
        sa.Column("instructions", sa.Text(), nullable=False, server_default=sa.text("''")),
        schema="control",
    )


def downgrade() -> None:
    op.drop_column("skill_artifact", "instructions", schema="control")
