"""agent direct-effect contract migration (V1.7 D04)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-10

Drops the Agent Draft/Published lifecycle columns. Live columns
(name/description/instructions/model_config_id/memory_policy) already hold
the current revision, so no data backfill is required. The `status` column
is dropped as well: enable/disable is served by `enabled`.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_STATEMENTS = [
    "ALTER TABLE agent_definition DROP COLUMN IF EXISTS draft_payload",
    "ALTER TABLE agent_definition DROP COLUMN IF EXISTS published_payload",
    "ALTER TABLE agent_definition DROP COLUMN IF EXISTS published_hash",
    "ALTER TABLE agent_definition DROP COLUMN IF EXISTS status",
]

DOWNGRADE_STATEMENTS = [
    "ALTER TABLE agent_definition ADD COLUMN IF NOT EXISTS draft_payload JSONB",
    "ALTER TABLE agent_definition ADD COLUMN IF NOT EXISTS published_payload JSONB",
    "ALTER TABLE agent_definition ADD COLUMN IF NOT EXISTS published_hash VARCHAR(128)",
    "ALTER TABLE agent_definition ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'draft'",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
