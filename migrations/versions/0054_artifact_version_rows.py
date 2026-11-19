"""Artifact version-as-row + OUTPUT type unification.

Each immutable version is its own ``runtime.artifact`` row; ``logical_file_id`` groups
the versions of one logical file and ``version`` is the per-logical identity sequence.
Development data may be cleared, but this migration is still safe for existing rows:
old rows become the first version of their own logical file.
"""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifact",
        sa.Column("logical_file_id", sa.Uuid(), nullable=True),
        schema="runtime",
    )
    op.add_column(
        "artifact",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        schema="runtime",
    )
    op.execute("UPDATE runtime.artifact SET logical_file_id = id WHERE logical_file_id IS NULL")
    op.execute(
        "ALTER TABLE runtime.artifact ALTER COLUMN logical_file_id SET DEFAULT gen_random_uuid()"
    )
    op.alter_column("artifact", "logical_file_id", nullable=False, schema="runtime")
    op.create_unique_constraint(
        "uq_artifact_logical_version",
        "artifact",
        ["tenant_id", "logical_file_id", "version"],
        schema="runtime",
    )
    op.create_index(
        "ix_artifact_logical_file",
        "artifact",
        ["tenant_id", "logical_file_id"],
        schema="runtime",
    )
    op.execute(
        "UPDATE runtime.artifact SET artifact_type = 'OUTPUT' "
        "WHERE artifact_type IN ('AGENT_OUTPUT', 'TASK_OUTPUT')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE runtime.artifact SET artifact_type = 'AGENT_OUTPUT' "
        "WHERE artifact_type = 'OUTPUT' AND run_id IS NOT NULL"
    )
    op.execute(
        "UPDATE runtime.artifact SET artifact_type = 'TASK_OUTPUT' "
        "WHERE artifact_type = 'OUTPUT' AND task_id IS NOT NULL"
    )
    op.drop_index("ix_artifact_logical_file", table_name="artifact", schema="runtime")
    op.drop_constraint("uq_artifact_logical_version", "artifact", schema="runtime")
    op.drop_column("artifact", "version", schema="runtime")
    op.drop_column("artifact", "logical_file_id", schema="runtime")
