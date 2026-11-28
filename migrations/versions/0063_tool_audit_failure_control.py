"""Failure-control facts on `runtime.tool_call_audit`.

`retryable` is nullable: legacy rows and unknown outcomes must stay "not declared",
not false. Dispositions and the bounded public diagnostic are only written for rows
that were completed by the new path.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

SCHEMA = "runtime"


def upgrade() -> None:
    op.add_column(
        "tool_call_audit",
        sa.Column("retryable", sa.Boolean(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tool_call_audit",
        sa.Column("retry_disposition", sa.String(16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tool_call_audit",
        sa.Column("agent_disposition", sa.String(16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tool_call_audit",
        sa.Column("diagnostic_json", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("tool_call_audit", "diagnostic_json", schema=SCHEMA)
    op.drop_column("tool_call_audit", "agent_disposition", schema=SCHEMA)
    op.drop_column("tool_call_audit", "retry_disposition", schema=SCHEMA)
    op.drop_column("tool_call_audit", "retryable", schema=SCHEMA)
