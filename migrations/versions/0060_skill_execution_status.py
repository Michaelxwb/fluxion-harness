"""Record Skill execution completion separately from business conclusions."""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_call_audit", sa.Column("execution_status", sa.String(16)), schema="runtime")
    op.alter_column("tool_call_audit", "business_status", type_=sa.String(32), schema="runtime")


def downgrade() -> None:
    op.drop_column("tool_call_audit", "execution_status", schema="runtime")
    op.execute(
        "UPDATE runtime.tool_call_audit SET business_status='UNKNOWN' "
        "WHERE length(business_status) > 16"
    )
    op.alter_column("tool_call_audit", "business_status", type_=sa.String(16), schema="runtime")
