"""Store the upstream business TraceId separately from Run tracing."""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("egress_audit", sa.Column("business_trace_id", sa.Uuid()), schema="runtime")


def downgrade() -> None:
    op.drop_column("egress_audit", "business_trace_id", schema="runtime")
