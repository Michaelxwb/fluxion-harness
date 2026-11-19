"""Business execution fields on `runtime.tool_call_audit` (TASK-008 / design §4.7).

Transport `status` only says whether the call completed. The new columns carry the
business verdict produced once by `ToolDefinition.outcome_parser`:
`business_status`, `failure_layer`, `exit_code`, `output_truncated`, `evidence_preview`.

Old rows keep `business_status='UNKNOWN'` and `NULL` for the rest: transport `OK` must
never be backfilled as business `SUCCEEDED`, and `NULL` means "not observed", not "no
content failure". Development data may be cleared; no evidence is synthesized.
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

SCHEMA = "runtime"


def upgrade() -> None:
    op.add_column(
        "tool_call_audit",
        sa.Column("business_status", sa.String(16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tool_call_audit",
        sa.Column("failure_layer", sa.String(16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tool_call_audit",
        sa.Column("exit_code", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tool_call_audit",
        sa.Column("output_truncated", sa.Boolean(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tool_call_audit",
        sa.Column("evidence_preview", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        "UPDATE runtime.tool_call_audit SET business_status = 'UNKNOWN' "
        "WHERE business_status IS NULL"
    )


def downgrade() -> None:
    op.drop_column("tool_call_audit", "evidence_preview", schema=SCHEMA)
    op.drop_column("tool_call_audit", "output_truncated", schema=SCHEMA)
    op.drop_column("tool_call_audit", "exit_code", schema=SCHEMA)
    op.drop_column("tool_call_audit", "failure_layer", schema=SCHEMA)
    op.drop_column("tool_call_audit", "business_status", schema=SCHEMA)
